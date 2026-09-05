# Google provider connectors  `stage-14.1.3`

This stage is a set of connectors for Google services. It is part of the main source-sync work: the system signs in with an authorized Google account, asks Google what data is available, converts that data into simpler records, and tracks changes so later runs do not have to reread everything.

Each file is a different “adapter” for a Google product. Gmail reads mailboxes and turns messages into searchable text while noticing new and deleted email. Drive lists files, shared drives, permissions, comments, and revisions. Docs and Sheets start from Drive file lists, then fetch document bodies or spreadsheet rows and flatten them into readable text records. Calendar reads events and also creates separate attendee records so invitees can be searched directly. Meet imports meeting transcripts and AI-written notes as recallable pages. Google Ads reads advertiser objects such as customers, campaigns, ads, and performance reports. The shared Google helper separates real permission problems from temporary quota limits, so the system can skip inaccessible data but retry when Google is only asking it to slow down.

## Files in this stage

### Gmail mailbox sync
Reads Gmail messages, renders them as searchable text, and tracks incremental mailbox changes.

### `extensions/sources/ufo_ext_sources/providers/gmail.py`

`io_transport` · `source sync and record rendering`

Gmail does not store an email as one simple text field. A message is a nested MIME tree, which means the readable body may be buried in plain-text or HTML parts, and the useful labels like From, To, and Subject live in headers. This file bridges that gap. It talks to Gmail’s REST API, finds message IDs, fetches full message bodies, decodes Gmail’s URL-safe base64 body format, and flattens each message into a record the rest of the system can store.

The connector has two main modes. On the first run, it backfills messages from a pinned time window, like “the last 30 days,” and then saves Gmail’s history ID as a bookmark. On later runs, it asks Gmail for changes since that bookmark. New message IDs are fetched and deleted message IDs become tombstones, which tell the rest of the system that a record should disappear.

The file also makes emails pleasant to recall later. Instead of dumping raw Gmail JSON, it renders a message like a human-readable email: title, sender, recipients, subject, and body. If only HTML exists, it strips tags and keeps readable text. If Gmail says the saved history bookmark is too old, the connector signals that the cursor expired so the core system can start over cleanly.

#### Function details

##### `GmailConnector.paginate_source`  (lines 94–104)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector-facing entry for reading Gmail pages. It accepts the sync cursor and optional backfill cutoff, then delegates to the Gmail-specific pagination logic.

**Data flow**: It receives an HTTP client, stream description, saved cursor, user ID, and optional earliest date. It ignores the user ID here, passes the cursor and cutoff into `paginate`, and returns the async stream of pages that `paginate` produces.

**Call relations**: The wider source runner calls this when it wants Gmail records. This method immediately hands the real work to `GmailConnector.paginate`, keeping the connector compatible with the common source interface.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 106–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main read loop for the Gmail messages stream. It decides whether to do an initial backfill or an incremental change check, then yields pages of records and deletes.

**Data flow**: It takes the stream, an HTTP client, a cursor, and maybe a backfill date. With no cursor, it asks `_backfill` for message IDs and a new Gmail history bookmark. With a cursor, it asks `_history` for new and deleted IDs. It fetches full bodies for new IDs in chunks with `_fetch_bodies`, then emits `StreamPage` objects containing records, deletes, and the next cursor. If Gmail rejects access because the account lacks the needed scope, it turns that into a skipped stream rather than a failed run.

**Call relations**: `paginate_source` calls this as the core Gmail flow. It calls `_backfill` or `_history` to learn what changed, `_fetch_bodies` to turn IDs into records, and `google.refused_for_scope` to distinguish missing permission from other HTTP errors.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 2 external calls (__init__, refused_for_scope).


##### `GmailConnector._backfill`  (lines 145–170)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time scan of a mailbox for messages inside the configured backfill window. It also chooses the history bookmark that future incremental syncs should start from.

**Data flow**: It reads the mailbox’s current profile history ID first as a safety floor. It then lists Gmail message IDs page by page, optionally using a query like “after this timestamp.” After collecting IDs, it asks `_seed_history_id` for the best next cursor and returns the IDs plus that cursor.

**Call relations**: `paginate` calls this when there is no saved cursor yet. It calls `_profile_history_id` before listing messages and `_seed_history_id` after listing so the next run can switch from backfill mode to change-tracking mode.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 172–175)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail’s current mailbox history ID from the profile endpoint. That ID acts like a bookmark for “where the mailbox was” before a backfill scan began.

**Data flow**: It sends a request to Gmail’s profile endpoint through the shared HTTP helper. It looks for a string `historyId` in the response and returns it, or returns nothing if Gmail did not provide a usable value.

**Call relations**: `_backfill` calls this before listing messages. The value it returns is passed into `_seed_history_id` as a fallback cursor, especially important when the backfill window contains no messages.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 177–201)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the history ID that should be saved after a backfill. Its job is to avoid getting stuck re-running the same backfill forever while also avoiding skipping mail that arrived during the scan.

**Data flow**: It receives the collected message IDs and the earlier profile history ID. If there are messages, it fetches the newest listed message in minimal form and uses that message’s `historyId` if available. If the message disappeared or no suitable value exists, it falls back to the profile history ID.

**Call relations**: `_backfill` calls this after it finishes listing IDs. It may make one extra Gmail request to inspect the newest message, and then hands a cursor back to `_backfill` for `paginate` to save.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 203–238)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log since the last saved history ID. It finds which messages were added and which were deleted so the sync can update stored data without scanning the whole mailbox again.

**Data flow**: It receives an HTTP client and a saved history ID. It pages through Gmail’s history endpoint, collecting message IDs from added and deleted entries. It removes IDs that were both added and deleted, keeps the latest returned history ID, and returns new IDs, deleted IDs, and the next cursor. If Gmail says the old history ID is gone, it raises `CursorExpired` so the system can do a fresh backfill.

**Call relations**: `paginate` calls this whenever a cursor exists. Inside the loop it uses `_message_ids` to pull clean message IDs out of Gmail’s nested history entries.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 240–254)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns a list of Gmail message IDs into full flattened message records. It is the step that moves from “we know this message exists” to “we have readable content to store.”

**Data flow**: It receives message IDs, fetches each message from Gmail in full format, skips any message that vanished with a 404, and raises other HTTP errors. Each successful raw Gmail response is passed to `_flatten_message`, and the resulting records are returned as a list.

**Call relations**: `paginate` calls this for each chunk of newly added IDs. `_fetch_bodies` then hands each raw Gmail message to `_flatten_message`, which extracts headers, recipients, labels, and body text.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 256–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a stored Gmail record into prose that a person can read and the recall system can search well. It avoids showing raw Gmail JSON, which would be noisy and hard to understand.

**Data flow**: It receives a flattened record and stream description. For the messages stream, it chooses a title from the subject or the default renderer, formats From, To, Cc, and Subject lines, chooses the best body text with `_message_body`, and returns both the title and the full rendered text.

**Call relations**: The source framework uses this override when Gmail records need display or recall text. It calls `_str`, `_format_contact`, `_format_recipients`, and `_message_body` to build the email-like output.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 279–288)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts message IDs from Gmail history entries. Gmail wraps each ID in nested objects, so this helper pulls out only the usable string IDs.

**Data flow**: It receives an arbitrary value that should be a list of history entries. It ignores malformed entries, looks inside each entry’s `message` object, keeps non-empty string IDs, and returns those IDs as a list.

**Call relations**: `GmailConnector._history` calls this separately for added-message and deleted-message sections. It gives `_history` clean ID lists so the change calculation can work with simple sets.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 291–318)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This reshapes one raw Gmail message into the simple record format used by the sync system. It lifts the important pieces out of Gmail’s nested payload structure.

**Data flow**: It receives the full raw Gmail message. It reads selected headers, extracts plain-text and HTML bodies, parses sender and recipients, copies labels, and determines direction as outbound if the message has Gmail’s `SENT` label. It returns one flat dictionary with fields like subject, from address, recipients, labels, and body text.

**Call relations**: `GmailConnector._fetch_bodies` calls this after fetching each full message. `_flatten_message` relies on `_extract_bodies`, `_parse_first_address`, and `_addresses` to decode the parts that Gmail stores in specialized formats.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 321–335)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This finds the first plain-text body and first HTML body inside Gmail’s nested MIME payload. MIME is the email format that lets one message contain many parts, such as text, HTML, and attachments.

**Data flow**: It receives the payload tree from a Gmail message. It walks through the root part and all child parts, decodes the first `text/plain` and first `text/html` body it finds, and returns them as a pair. Missing bodies come back as `None`.

**Call relations**: `_flatten_message` calls this while building a flat record. Its inner `walk` function does the tree traversal and calls `_b64url_decode` when it finds encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 325–332)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This inner helper recursively visits each part of an email’s MIME tree. It is like checking every folder inside a folder until it finds the readable message parts.

**Data flow**: It receives one MIME part at a time. If the part is plain text or HTML and has body data, it decodes that data and saves it if that body type has not already been found. Then it visits any child parts in the same way.

**Call relations**: `_extract_bodies` defines and uses this helper during body extraction. When encoded text is found, it hands the raw Gmail body string to `_b64url_decode`.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 338–344)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body text format into normal Unicode text. Gmail uses URL-safe base64, a text-safe encoding, and may omit the usual padding characters.

**Data flow**: It receives an encoded string. It adds any missing padding, decodes it with URL-safe base64, converts bytes to UTF-8 text while replacing invalid characters, and returns the result. If decoding fails, it returns an empty string.

**Call relations**: `_extract_bodies.walk` calls this when it finds a plain-text or HTML MIME body. It relies on Python’s base64 decoder to do the low-level conversion.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 347–354)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses the first email address from a header such as `From`. It separates the actual address from the display name.

**Data flow**: It receives a header string or nothing. If present, it asks Python’s email parser to split names and addresses, takes the first pair, lowercases the address, and returns address plus display name. Empty or unparseable input returns two `None` values.

**Call relations**: `_flatten_message` calls this for the sender field. It uses `email.utils.getaddresses` so it does not have to hand-parse tricky email address formats.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 357–364)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses all addresses from a recipient header such as `To` or `Cc`. It turns a single header string into a list of small address records.

**Data flow**: It receives a header string or nothing. It uses Python’s email parser to find each name and address, keeps entries with an address, lowercases the address, and returns dictionaries containing `handle` and `display_name`.

**Call relations**: `_flatten_message` calls this for the `to` and `cc` headers. The formatted records it returns are later used by rendering helpers to show readable recipient lines.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 367–372)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable email identity. It produces either `Name <address>` or just the address.

**Data flow**: It receives a possible email handle and display name. If the handle is not a non-empty string, it returns an empty string. Otherwise it combines the display name and handle when a name exists, or returns the handle alone.

**Call relations**: `GmailConnector.render` calls this for the sender. `_format_recipients` also calls it for each recipient before joining them into one line.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 375–382)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient records into one comma-separated line for display. It makes the stored recipient data look like a normal email header.

**Data flow**: It receives a value that should be a list. If it is not a list, it returns an empty string. For each dictionary item, it formats the contact with `_format_contact` and joins the results with commas.

**Call relations**: `GmailConnector.render` calls this when building `To` and `Cc` lines. It delegates each individual contact to `_format_contact` so sender and recipient formatting stay consistent.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 385–393)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for a rendered email. It prefers plain text, falls back to cleaned HTML, and uses Gmail’s snippet only as a last resort.

**Data flow**: It receives a flattened message record. If `body_text` contains non-blank text, it returns that. Otherwise it checks `body_html` and converts it to readable text. If neither body exists, it returns the trimmed snippet when available, or an empty string.

**Call relations**: `GmailConnector.render` calls this after building the header lines. This helper provides the message content that appears under the rendered From, To, Cc, and Subject block.

*Call graph*: called by 1 (render).


##### `_str`  (lines 396–397)

```
def _str(value: Any) -> str
```

**Purpose**: This safely treats a value as text only if it really is a string. It prevents non-string data from becoming an accidental title or header value.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `GmailConnector.render` calls this when reading the subject. That lets render use the subject only when the flattened record contains a real text value.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 404–406)

```
def __init__(self) -> None
```

**Purpose**: This prepares the small HTML-to-text parser used for email bodies. It creates a place to collect readable text as the parser scans HTML.

**Data flow**: It receives no outside data beyond the new parser object. It initializes the base `HTMLParser` with automatic character reference conversion and creates an empty list for text pieces.

**Call relations**: This constructor is used when an `_HtmlText` parser instance is created. The parser’s later methods add text and line breaks into the list prepared here.


##### `_HtmlText.extract`  (lines 409–414)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This is the simple public entry for turning an HTML email body into plain text. It removes tags, keeps visible words, and preserves useful line breaks.

**Data flow**: It receives raw HTML text. It creates a parser, feeds the HTML into it, joins the collected pieces, normalizes extra spaces on each line, removes empty lines, and returns clean text.

**Call relations**: This class method coordinates the `_HtmlText` parser methods. As the HTML parser reads input, `handle_data`, `handle_starttag`, and `handle_endtag` contribute the pieces that `extract` later cleans up.


##### `_HtmlText.handle_data`  (lines 416–417)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records visible text found inside an HTML body. It keeps the words that a person would actually see in the email.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that chunk to the parser’s internal list and returns nothing.

**Call relations**: The `HTMLParser` machinery calls this while processing HTML. The collected text becomes part of the final output produced by `_HtmlText.extract`.


##### `_HtmlText.handle_starttag`  (lines 419–421)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag represents a block-like boundary, such as a paragraph or table row. That keeps separate sections from running together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the parser’s text list. Attributes are ignored because only readable text matters here.

**Call relations**: The `HTMLParser` machinery calls this as it sees opening tags. Its newline markers are later cleaned and folded into the plain-text result by `_HtmlText.extract`.


##### `_HtmlText.handle_endtag`  (lines 423–425)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break after closing block-like HTML tags. It helps the cleaned email body keep paragraph and section boundaries.

**Data flow**: It receives the tag name. If the tag is one of the known block tags, it appends a newline marker to the parser’s text list and returns nothing.

**Call relations**: The `HTMLParser` machinery calls this as it sees closing tags. Together with `handle_starttag` and `handle_data`, it supplies the raw pieces that `_HtmlText.extract` turns into final readable text.


### Google API failures
Classifies Google access errors so permission gaps can be skipped while quota exhaustion triggers retryable failure.

### `extensions/sources/ufo_ext_sources/providers/google.py`

`domain_logic` · `request handling during source sync error classification`

Google APIs often report several different problems with the same HTTP status codes, especially 401 and 403. Those numbers can mean “this account does not have permission for this data,” but they can also mean “you have hit a usage limit for now.” Treating those as the same would cause bad behavior. If a quota limit were mistaken for a permission problem, the system might skip or park a stream that would have worked again after the quota window reset. If a permission problem were mistaken for a temporary outage, the system would keep retrying something that cannot succeed without a broader account grant.

This file is a small classifier for those Google errors. It looks inside an `httpx.HTTPStatusError`, which is an HTTP failure object from the `httpx` web request library. First it safely pulls out Google’s nested `error` details from the response body, if they exist. Then it checks whether those details use Google’s known quota signals, such as `RESOURCE_EXHAUSTED` or specific usage-limit reasons. Finally it answers the key question for the rest of the connector: “Was this refusal caused by insufficient permission scope?” In plain terms, it separates “you are not allowed” from “come back later.”

#### Function details

##### `error_detail`  (lines 31–38)

```
def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This function extracts the main Google `error` object from a failed HTTP response. It gives the rest of the file a safe dictionary to inspect, even when the response body is missing, malformed, or not in the expected shape.

**Data flow**: It receives an `httpx.HTTPStatusError`, reads the JSON body from its response, and looks for the nested `error` field. If the body is not valid JSON, or if the expected pieces are not dictionaries, it returns an empty dictionary instead of crashing. The result is a clean error-detail dictionary for later checks.

**Call relations**: `refused_for_scope` calls this first when it needs to understand what kind of Google refusal occurred. Inside, it relies on `ufo.sdk.sources.dict_or_empty` to turn uncertain response data into a safe dictionary before handing the details back.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (dict_or_empty).


##### `is_quota_refusal`  (lines 41–45)

```
def is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This function decides whether a Google refusal is really a usage-limit problem rather than a permission problem. It recognizes Google’s standard quota signals so the caller knows this error should be retried later instead of treated as a permanent lack of access.

**Data flow**: It receives a dictionary of Google error details. It first checks whether the top-level status is `RESOURCE_EXHAUSTED`, Google’s name for exhausted quota. If not, it looks through the detailed error list for known quota-related reasons such as `quotaExceeded` or `rateLimitExceeded`. It returns `True` for quota problems and `False` otherwise.

**Call relations**: `refused_for_scope` calls this after getting the parsed Google error details. This function uses `ufo.sdk.sources.list_or_empty` so that missing or oddly shaped `errors` data can be treated like an empty list instead of causing a failure.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (list_or_empty).


##### `refused_for_scope`  (lines 48–53)

```
def refused_for_scope(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This is the main decision function in the file. It answers whether an HTTP failure should be treated as a settled permission-scope refusal, meaning the connector cannot access that stream with the current grant and should skip it rather than retrying.

**Data flow**: It receives an `httpx.HTTPStatusError`. It checks whether the response status code is one of Google’s refusal codes, 401 or 403. If so, it extracts the response’s Google error details with `error_detail` and asks `is_quota_refusal` whether the refusal is actually a temporary quota limit. It returns `True` only when the status is a refusal and it is not a quota problem.

**Call relations**: This function ties the two helper checks together for callers elsewhere in the source connector. When a Google request fails, the caller can use this function to decide whether to turn the failure into a skipped stream or let the error rise so the wider sync system can apply retry and backoff behavior.

*Call graph*: calls 2 internal fn (error_detail, is_quota_refusal).


### Ads and calendar records
Ingests structured Google business and scheduling data from Google Ads and Google Calendar.

### `extensions/sources/ufo_ext_sources/providers/googleads.py`

`io_transport` · `source sync`

Google Ads is not a simple single-account data source. A signed-in advertiser can have access to several customer accounts, and every data pull must include both an OAuth credential and a separate Google Ads developer token. This file is the connector that knows those Google-specific rules.

The connector defines the streams it can read: customers, campaigns, ad groups, ads, campaign metrics, and customer-client relationships. When a sync starts, it creates an HTTP client for Google Ads, adds the required developer token header, and optionally adds a manager-account login customer ID. Think of this like showing both your personal ID and a special building pass before entering.

For each stream, it builds a Google Ads Query Language query. GAQL is Google Ads' SQL-like query format. The connector first asks Google which customer accounts are accessible, then runs the query for each customer. Rows from each account are stamped with that customer ID so later records do not lose where they came from.

Some streams come back deeply nested, for example campaign data inside a `campaign` object. The `flatten` method lifts the most important fields to the top level, such as IDs, names, dates, and metrics. If Google refuses access with an authorization error, the stream is skipped rather than crashing the whole sync.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token required for every API request. OAuth proves who the advertiser is, but Google Ads also requires this separate approved token, so the connector cannot safely run without it.

**Data flow**: It reads the environment variables `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` and `GOOGLE_ADS_DEVELOPER_TOKEN`. If either contains a token, it returns that text. If neither is set, it raises `StreamSkipped`, which tells the sync system to skip Google Ads instead of failing with a confusing API error.

**Call relations**: The HTTP-client setup calls this before making Google Ads requests. Its returned token is handed to `_make_client`, which places it into the request headers Google Ads expects.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Google Ads. It adds the Google Ads-specific headers that are not covered by the normal OAuth credential.

**Data flow**: It receives a base URL and a credential object from the source runner. It first lets the parent REST connector create the normal authenticated client, then adds the developer token header. It also reads an optional login customer ID from the environment, removes dashes if present, and adds it as another header. The result is an HTTP client ready to call Google Ads endpoints.

**Call relations**: This is used when the connector is being set up for network calls. It relies on `_developer_token` for the required Google Ads developer token before later methods use the client to list customers and run queries.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. It produces the list of account IDs that later queries must run against.

**Data flow**: It takes an authenticated HTTP client and calls Google's accessible-customers endpoint. From the response, it looks for resource names like `customers/1234567890`, strips off the `customers/` prefix, and returns a plain list of customer ID strings. Invalid or unexpected entries are ignored.

**Call relations**: `_query_each_customer` calls this first so it knows which accounts to visit. Without this step, the connector would not know where to run the stream queries.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one Google Ads query for one customer account and collects the rows returned by Google. It hides the batch-shaped response format so the rest of the connector can think in simple lists of records.

**Data flow**: It receives an HTTP client, a customer ID, and a GAQL query string. It posts the query to that customer's `searchStream` endpoint. Google returns an array of batches, each batch may contain a `results` list, and this function pulls all dictionary-shaped rows into one list. It returns that list of raw Google Ads rows.

**Call relations**: `_query_each_customer` calls this once for each accessible customer. The rows it returns are then tagged with the customer ID before being yielded back to `paginate`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every customer account the credential can reach. It is the bridge between a stream-level question, such as 'give me campaigns', and the many separate advertiser accounts where the answer may live.

**Data flow**: It receives an HTTP client and a query string. It first gets all accessible customer IDs, then sends the query to each customer. For every non-empty result set, it adds a `customer_id` field to each row and yields that group of rows as a page.

**Call relations**: `paginate` calls this after choosing the correct query for a stream. Internally, it depends on `_customer_ids` to discover accounts and `_search_stream` to fetch rows from each one.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function decides what Google Ads query to run for each supported stream and yields the results in pages. It is the main read path for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it builds the matching GAQL query. For campaign metrics, the cursor controls the starting date; if there is no cursor, it defaults to roughly the last 90 days. It then asks `_query_each_customer` to run that query across accessible accounts and yields each page of rows. If the stream is unknown, or if Google refuses access with a 401 or 403 response, it raises `StreamSkipped`.

**Call relations**: The source-sync framework calls this when it needs records for a stream. `paginate` delegates the repeated per-customer work to `_query_each_customer`, and its output later flows to `flatten` so records can be normalized for storage.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function turns nested Google Ads rows into simpler records with important fields at the top level. That makes records easier for the sync system to key, compare, and store.

**Data flow**: It receives one raw record and the stream it belongs to. For customers, it extracts a stable ID and name. For campaigns, it extracts the resource name, name, status, and start date. For campaign metrics, it builds a unique ID from customer, campaign, and date, then pulls out date, campaign ID, impressions, clicks, and cost. Streams without special flattening are returned unchanged.

**Call relations**: After `paginate` yields raw Google Ads rows, the wider connector flow can call `flatten` before writing records. It uses `dict_or_empty` so missing nested objects behave like empty dictionaries instead of causing errors.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the project's source-sync system. Without it, the system would not know how to ask Google for calendar events, how to keep only new changes after the first run, or how to represent cancelled events and attendees in its own storage.

The main class, GoogleCalendarConnector, reads from Google's events API. On the first run, it looks back 90 days and asks Google for events, including deleted ones. Google returns a sync token, which is like a bookmark saying, “next time, start from here.” Later runs send that token so only changed events are returned. If Google says the bookmark has expired, the connector raises CursorExpired so the wider system can start fresh. If the user did not grant calendar permission, it raises StreamSkipped so the run is recorded as skipped rather than broken.

The file exposes two views of the same Google data. The calendar_events stream keeps one record per event, including title, time, place, description, organizer, and a compact attendee list. The event_attendees stream “explodes” each event into separate attendee rows, like turning one party invitation into a guest list. The render method turns event records into readable text for recall or search.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 51–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads Google Calendar events page by page and yields them in the format the sync engine expects. It supports both the main event stream and the per-attendee stream, while preserving Google’s sync token so later runs only fetch changes.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. If there is a cursor, it sends it to Google as a sync token; otherwise it starts from a 90-day lookback window and asks Google to include deleted events. For each page Google returns, it filters out unusable items, turns active events into records, turns cancelled events into delete markers for the event stream, and sends each batch out as a StreamPage. At the end, it includes Google’s next sync token as the new cursor. If Google reports an expired token, it turns that into CursorExpired; if Google refuses because calendar permission is missing, it turns that into StreamSkipped.

**Call relations**: This is the main entry point the source sync engine calls when it wants Google Calendar data. During the walk through Google’s pages, it hands normal event records to _flatten_event and attendee-only records to _flatten_attendees. It relies on Google’s scope-check helper to tell the difference between a missing permission and other API failures, then reports the right outcome back to the larger sync system.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 5 external calls (__init__, __init__, now, timedelta, refused_for_scope).


##### `GoogleCalendarConnector.render`  (lines 111–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced calendar event into a human-readable title and body of text. This matters because the stored record is structured data, but search and recall work better when an event can also be read like a short note.

**Data flow**: It receives one synced record and the stream it came from. If the stream is not calendar_events, it lets the parent connector use its default rendering. For calendar events, it pulls out the title, start and end time, location, attendee email handles, and description, then joins them into a clean text block. It returns the event title and the rendered body.

**Call relations**: The sync or indexing layer calls this when it needs text to store or search. It uses _str to safely turn a possibly missing or non-text title into an empty string instead of failing. For streams other than calendar_events, it passes control back to the base RestConnector behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 139–166)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the project’s standard event record. It keeps the useful calendar details and folds attendee summaries into the event so the event can be recalled with who was invited.

**Data flow**: It receives a raw event dictionary from Google. It reads fields such as id, creation and update time, summary, description, location, start and end time, organizer, status, recurring-event information, and attendees. It normalizes the organizer email to lowercase, turns the start and end values into consistent timestamp strings, and converts each valid attendee through _attendee. It returns one flat dictionary ready to be written by the sync system.

**Call relations**: GoogleCalendarConnector.paginate calls this for active events in the calendar_events stream. While building the record, it asks _parse_when to normalize Google’s two different time formats and _attendee to simplify each attendee entry.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 169–175)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Creates a small, consistent attendee summary for embedding inside an event record. It keeps only the invitee’s email handle, display name, and response status.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee email, copies the display name, and maps Google’s response wording, such as needsAction, into the project’s preferred wording, such as needs_action. It returns a compact attendee dictionary.

**Call relations**: _flatten_event calls this while building the attendee list that lives inside a calendar event record. It does not call further project helpers; it simply translates Google’s attendee shape into the local shape.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 178–204)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one Google Calendar event into separate records, one for each attendee. This makes attendees queryable as their own rows instead of being visible only inside the event.

**Data flow**: It receives a raw Google event. It reads the event id, timestamps, organizer email, and attendee list. For every attendee with a valid email address, it lowercases the email, builds a unique id from the event id and email handle, maps the response status, records whether this attendee is the current user, and asks _attendee_role to decide whether the attendee is an organizer, resource, optional guest, or required guest. It returns a list of attendee-row dictionaries.

**Call relations**: GoogleCalendarConnector.paginate calls this when syncing the event_attendees stream. It delegates the role decision to _attendee_role so the row-building code stays focused on shaping each attendee record.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 207–214)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in an event. The result helps distinguish organizers, room or equipment resources, optional guests, and required guests.

**Data flow**: It receives one attendee dictionary and a flag saying whether that attendee’s email matches the event organizer. It checks the organizer indicators first, then whether the attendee is a resource, then whether they are optional. It returns one role string: organizer, resource, optional, or required.

**Call relations**: _flatten_attendees calls this for each attendee row it creates. Its answer becomes the role field stored in the per-attendee stream.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 217–226)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar’s event time format into one timestamp string. Google uses one shape for timed events and another for all-day events, so this helper gives the rest of the connector one simpler format to use.

**Data flow**: It receives a value that may be a Google time dictionary. If the value is not a dictionary, it returns nothing. If it contains dateTime, it returns that timestamp as text. If it contains date for an all-day event, it turns the date into a midnight UTC-style timestamp. If neither field is present, it returns nothing.

**Call relations**: _flatten_event calls this for both the event start and end fields. That lets event records store starts_at and ends_at without the rest of the connector caring which Google time shape was used.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 229–230)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents rendering from accidentally treating missing or non-text data as an event title.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: GoogleCalendarConnector.render calls this when preparing the event title. It is a tiny guardrail that keeps the rendering path simple and safe.

*Call graph*: called by 1 (render).


### Drive content and meeting artifacts
Syncs Google Drive-hosted files and related document, meeting, and spreadsheet content into searchable records.

### `extensions/sources/ufo_ext_sources/providers/googledocs.py`

`io_transport` · `source sync`

This connector is the system’s read-only bridge to Google Docs. Google stores document files in Drive, but the actual document contents come from the Docs API, so this file uses both services like a two-step lookup: first it asks Drive, “Which Google Docs can this grant see?”, then it asks Docs, “What is inside each one?”

The connector supports incremental syncing. That means it does not have to reread everything every time. It uses a saved timestamp cursor and asks Drive only for documents modified after that time. Drive returns files in pages, and the connector fetches each document by its file id. It then builds one flat record containing useful fields such as title, URL, creation time, update time, file metadata, and the full Docs response.

A notable safety feature is that one bad document does not ruin the whole sync. If Drive lists a file but Docs refuses or cannot find it, the connector returns a small placeholder record instead of failing. But if the whole Drive listing is refused because the grant lacks permission, the stream is skipped with a clear message.

Finally, Google Docs text is buried in a nested tree of paragraphs and text runs. The render path walks that tree and joins the visible text into prose, giving the rest of the system a simple text version to index or display.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 51–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the Google Docs stream. It collects Google Doc files from Drive, fetches each document’s contents, packages them into sync records, and yields them in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It asks _iter_doc_files for Drive file pages newer than that cursor, then uses each file id to call _document. For each successful or placeholder document, it combines the Docs data with Drive metadata such as title, URL, created time, and modified time. It outputs lists of records, yielding a page whenever enough records have been collected, and yields any final partial page at the end. If Google refuses access because the grant lacks the right scope or sharing, it turns that into a StreamSkipped error instead of a generic HTTP failure.

**Call relations**: This method is the connector’s page-producing entry point during a sync. It relies on _iter_doc_files to find candidate Google Docs and on _document to fetch each one in full. If an HTTP permission error happens, it asks google.refused_for_scope whether the problem is a missing permission grant; when it is, it raises StreamSkipped so the larger sync can move on cleanly.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files); 1 external calls (refused_for_scope).


##### `GoogleDocsConnector._document`  (lines 89–98)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one Google Doc by id from the Google Docs API. It also protects the sync from failing when an individual document was listed by Drive but cannot be opened.

**Data flow**: It receives an HTTP client and a Google file id. It sends a GET request to the Docs API for that document. If the request succeeds, the full document JSON comes back. If Google returns 403 or 404, meaning the document is forbidden or missing, it returns a small stub containing only the document id. Other HTTP errors are passed upward unchanged.

**Call relations**: paginate calls this once for each Drive file id it wants to turn into a synced record. The result goes back to paginate, which adds Drive metadata around it before yielding it to the rest of the sync process.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 100–127)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through Google Drive’s file list and yields pages of files that are Google Docs. It is responsible for applying the modified-time cursor so the sync can be incremental.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive query for untrashed Google Docs, and if a cursor is present, it adds a condition for documents modified after that time. It repeatedly calls the Drive files endpoint, using Google’s next page token to continue. Each response’s files field is normalized through list_or_empty, then non-empty file lists are yielded. When there is no valid next page token, it stops.

**Call relations**: paginate depends on this method to supply the Drive-side list of documents. This method does not fetch document bodies itself; it only finds file metadata and hands those file records back so paginate can call _document for the contents.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 129–134)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a title and a plain-text body that the rest of the system can read, index, or show. It adds a simple heading so the text has context.

**Data flow**: It receives a record and its stream description. It pulls out the title if it is a string, asks _plain_text to extract readable text from the document body, and builds a rendered string with a heading followed by the document text. It returns two things: the title and the rendered prose.

**Call relations**: This method is used after records have been fetched, when the system needs a human-readable version of a document. It delegates the nested Google Docs text extraction to _plain_text, then wraps that text in a small stream-specific heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 137–153)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts the visible paragraph text from a Google Docs API record. It turns Google’s nested document structure into one simple string.

**Data flow**: It receives a document record. It looks for body.content, then walks each paragraph and each paragraph element. Whenever it finds a textRun with string content, it appends that text to a list. At the end, it joins all text pieces in order and trims extra whitespace. Non-paragraph items, missing fields, and unexpected shapes are ignored.

**Call relations**: GoogleDocsConnector.render calls this when it needs the readable body of a document. _plain_text does the low-level tree walking, and render uses its result to build the final text representation.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledrive.py`

`io_transport` · `source sync`

This connector is the project’s read-only bridge to Google Drive. Without it, the system would not know how to ask Google for Drive files, how to keep up with later changes, or how to notice that a file was deleted or moved to the trash.

The main idea is streaming. For the files stream, the first run lists all live, untrashed files. At the end of that first run, it asks Google for a special changes token, like a bookmark in a logbook. Future runs use that bookmark to read only what changed: new or updated files become records, while removed or trashed files become delete notices. If Google says the bookmark is too old, the connector reports that the cursor expired so the wider system can start fresh.

Shared drives are simpler: they are re-listed each time. Permissions, comments, and revisions are child collections, so the connector first walks through every file, then asks Google for that file’s related items.

The file also deals with access limits. If the user’s grant does not include Drive access, the stream is marked as skipped instead of failed. But ordinary unexpected errors still bubble up. Finally, `render` turns a Drive file into a small human-readable text block with its name, MIME type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 87–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Google Drive streams. Given a stream name, it chooses the right way to fetch that kind of Drive data and yields pages of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the saved place to resume from. For files, it either reads changes from the saved cursor or performs the first full listing and then saves a new changes token. For shared drives, it lists drives. For permissions, comments, and revisions, it walks through files and then reads each file’s child items. It outputs record pages or cursor updates, and it may turn a missing Drive permission into a clean “stream skipped” result.

**Call relations**: The wider source framework calls this when it wants data for a Google Drive stream. This function then hands the work to `_paginate_file_changes`, `_paginate_files`, `_start_page_token`, `_paginate_shared_drives`, or `_paginate_file_children` depending on the stream. If Google refuses access because the grant lacks the right scope, it uses the Google helper to recognize that and raises `StreamSkipped` so the run records a skip rather than a hard failure.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 2 external calls (__init__, refused_for_scope).


##### `GoogleDriveConnector._paginate_files`  (lines 121–146)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists Google Drive files in pages. It is used for the initial full file sync, and also as the file list that child streams use when they need to look up permissions, comments, or revisions for each file.

**Data flow**: It receives an HTTP client and an optional time cursor. It builds a Google Drive file search for untrashed files, optionally only those modified after the cursor, then repeatedly asks the Drive API for the next page. Each response’s `files` list is cleaned into an ordinary list, yielded if non-empty, and the next page token is followed until there are no more pages.

**Call relations**: `paginate` calls this during a first-time files sync. `_paginate_file_children` also calls it so it can visit every file before fetching that file’s related items. It relies on `list_or_empty` to safely treat missing or malformed response lists as empty lists.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 148–153)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This function asks Google Drive for the current changes bookmark. That bookmark lets later sync runs read only changes instead of scanning every file again.

**Data flow**: It receives an HTTP client, calls Google’s start-page-token endpoint, and reads the `startPageToken` field from the response. If the token is a real non-empty string, it returns it; otherwise it returns nothing.

**Call relations**: `paginate` calls this after finishing the first full file listing. The returned token is yielded as the next cursor, giving the rest of the sync system a safe point from which future change syncs can resume.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 155–200)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This function reads Google Drive’s change feed for files. It is how later sync runs find updated files and also learn which files should be deleted from the local copy.

**Data flow**: It receives an HTTP client and a saved changes cursor. It asks Google for changes page by page. For each change, it checks the file id, separates removed or trashed files into delete notices, and collects live file objects as records. Each output page contains records, delete ids, and the next cursor. If Google returns status 410, meaning the saved cursor has expired, it raises `CursorExpired` so the system can perform a fresh sync.

**Call relations**: `paginate` calls this when syncing the files stream with an existing cursor. This function creates `StreamPage` objects because file changes may include both new data and deletion instructions. It uses `list_or_empty` to safely read Google’s `changes` array.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 202–219)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists the shared drives visible to the grant. Shared drives are team-like Drive spaces, and this stream is refreshed by re-reading the full list each run.

**Data flow**: It receives an HTTP client, asks Google’s shared-drive endpoint for a page of drives, yields any drive records it finds, and follows Google’s next page token until there are no more pages. The result is a sequence of shared-drive record batches.

**Call relations**: `paginate` calls this for the `shared_drives` stream. Like the other list readers, it uses `list_or_empty` so an absent `drives` field behaves like an empty page rather than crashing the sync.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 221–259)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches per-file child data, such as permissions, comments, or revisions. It works by visiting each file first, then asking Google for the chosen child collection under that file.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It first gets all files through `_paginate_files`. For each valid file id, it calls the matching child endpoint, follows child pagination, optionally filters child records by the stream’s cursor field, and yields child records with extra `file_id` and `file_name` fields attached. If Google says a specific child collection is forbidden or missing for a file, it skips that file’s child data and keeps going.

**Call relations**: `paginate` calls this for the `permissions`, `comments`, and `revisions` streams. This function depends on `_paginate_files` to know which files to inspect, and uses `list_or_empty` to safely read each child collection from Google’s response.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 261–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a synced Google Drive file record into readable text. That text gives humans useful context, such as the file name, type, owners, and web link.

**Data flow**: It receives one record and the stream it came from. If the stream is not `files`, it lets the parent connector render it normally. For file records, it extracts the name, MIME type, owner names or emails, and view link, then returns a title and a compact text body.

**Call relations**: The source framework uses this when it needs a human-friendly representation of a synced record. For file records it uses the local `_str` helper to safely turn the file name into a string; for all other streams it hands off to the base `RestConnector` behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 279–280)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely returns a value only if it is already a string. It prevents non-text values from accidentally becoming titles in rendered output.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. If it is anything else, such as `None` or a number, it returns an empty string.

**Call relations**: `GoogleDriveConnector.render` calls this when building the title for a file record. It keeps the rendering path simple and avoids repeating the same type check inline.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googlemeet.py`

`io_transport` · `source sync`

This connector is the bridge between Google Meet’s web API and the project’s source-sync system. Its job is to find recent conference records, collect any generated transcripts or AI notes attached to them, and render each useful meeting as one readable page. Think of it like a meeting clerk: it checks the meeting register, gathers the transcript sheets and summary document, and files them together under one meeting title.

The main flow starts by listing Google Meet conference records. For incremental syncs, it uses the last seen meeting start time, but deliberately looks back one day. This matters because Google may create transcripts or notes after the meeting has ended, so a meeting that looked empty yesterday may have useful artifacts today.

For each conference, the connector asks for transcript sessions and smart-note sessions. Transcript sessions include speaker entries from the Meet API. Smart notes may point to a Google Docs document; when possible, this connector fetches that document and extracts plain text from it. If the document is missing or access is denied, the connector keeps the document link instead of failing the whole sync.

Finally, it renders a human-readable page with meeting metadata, transcript dialogue, and AI summaries. If Google refuses access because the account lacks the right permission, the stream is marked skipped rather than treated as a system failure.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 55–89)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main fetch loop for the Google Meet meeting artifacts stream. It asks Google for pages of conference records, turns conferences with transcripts or smart notes into records, and yields them to the sync system in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It builds Google Meet API query parameters, optionally applies a one-day lookback from the cursor, reads conference pages, expands each conference into a richer record, and outputs StreamPage objects containing records plus the next cursor. If Google refuses access because the grant lacks permission, it changes that failure into a skipped stream message.

**Call relations**: The source sync driver calls this when it needs Google Meet data. Inside the loop it uses _lookback to widen incremental syncing, _max_start_time to advance the cursor, and _conference_record to gather transcripts and notes for each meeting. When a permission error appears, it asks google.refused_for_scope whether the error should become a StreamSkipped result.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 3 external calls (__init__, list_or_empty, refused_for_scope).


##### `GoogleMeetConnector._conference_record`  (lines 91–114)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete internal record for one Google Meet conference. It gathers the conference’s transcripts and smart notes and packages them with the meeting’s basic details.

**Data flow**: It receives a raw conference dictionary from Google. It reads the conference resource name, asks for transcript and smart-note artifacts under that conference, converts each artifact into a cleaner record, and returns one dictionary containing the conference id, title, timing, space, transcripts, and smart notes.

**Call relations**: paginate calls this for every conference returned by Google. It hands off artifact listing to _artifacts, transcript shaping to _transcript, smart-note shaping to _smart_note, and uses _resource_id and _str to make safe, stable text fields.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 116–133)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind under a conference, such as all transcript sessions or all smart-note sessions. It hides Google’s page-by-page API shape from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the artifact collection name to read. If the parent is missing, it returns an empty list. Otherwise it repeatedly requests pages from Google, collects the artifact items from each response, follows any next-page token, and returns a single list.

**Call relations**: _conference_record calls this twice: once for transcripts and once for smart notes. It uses list_or_empty so that missing or oddly shaped response fields behave like empty lists instead of breaking the sync.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 135–147)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one raw Google Meet transcript session into the connector’s transcript record. It includes metadata, the linked Google Docs destination, and the individual spoken entries.

**Data flow**: It receives a transcript dictionary from Google. It reads its name, state, start and end times, extracts any Google Docs destination fields, fetches its transcript entries, and returns a cleaned dictionary ready to be stored inside the conference record.

**Call relations**: _conference_record calls this for every transcript artifact found by _artifacts. It delegates the line-by-line transcript fetch to _transcript_entries and uses _docs_destination, _resource_id, and _str to normalize the output.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 149–182)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual spoken lines inside a transcript. These entries are what later become readable dialogue in the rendered meeting page.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty, it returns no entries. Otherwise it requests all entry pages from Google, extracts each entry’s id, participant, text, language, and timing, and returns a list of cleaned entry dictionaries. If Google says the entry resource is forbidden or missing, it returns whatever it has collected so far rather than failing.

**Call relations**: _transcript calls this when building a transcript record. It uses _str and _resource_id to make safe identifiers and text, and list_or_empty to treat missing entry lists as empty.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 184–199)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a clean record. If the note points to a Google Docs document and that document is readable, it also adds the document’s plain text.

**Data flow**: It receives a raw smart-note dictionary. It extracts the note id, name, state, times, and Docs destination. If there is a document id, it asks _document_text for the document body; when text is returned, it adds that text under the note’s body field. The result is a dictionary that can be rendered as an AI summary section.

**Call relations**: _conference_record calls this for every smart-note artifact found under a conference. It uses _docs_destination, _resource_id, and _str for cleanup, and hands the optional Google Docs fetch to _document_text.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 201–210)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a Google Docs document and extracts simple plain text from it. It is used so smart notes can be included directly in the meeting page instead of only linked.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely URL-escapes the id, requests the document from the Google Docs API, and passes the returned document structure to _plain_text. If the document is missing or access is denied, it returns an empty string; other HTTP errors are allowed to fail normally.

**Call relations**: _smart_note calls this when a smart-note artifact includes a Docs document id. It uses quote to make the document id safe for a URL and _plain_text to turn the nested Docs response into readable text.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 212–229)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one collected meeting record into the final title and readable page text. It is the step that makes raw API data useful for search and recall.

**Data flow**: It receives a record and a stream description. For the meeting_artifacts stream, it reads the title, conference metadata, transcript list, and smart-note list, formats them into Markdown-like text, and returns the page title plus body. For any other stream, it falls back to the parent connector’s rendering behavior.

**Call relations**: The source framework calls this after records have been fetched. It relies on _labeled for compact metadata blocks, _transcripts_section for transcript text, _smart_notes_section for AI summaries, and _str to avoid non-text values leaking into the page.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 232–248)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript records for a meeting into a readable transcript section. It includes transcript metadata and speaker dialogue.

**Data flow**: It receives an unknown value that should contain transcripts. It turns that value into a safe list, skips the section if the list is empty, and for each transcript builds a metadata block plus dialogue text. It returns one combined string for the rendered page.

**Call relations**: GoogleMeetConnector.render calls this while building the meeting page. It uses _labeled for the transcript’s state, times, and document link, and _dialogue to turn transcript entries into human-readable speaker lines.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 251–267)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats all Gemini smart notes for a meeting into a readable AI summaries section. It shows note metadata and, when available, the note body text.

**Data flow**: It receives an unknown value that should contain smart notes. It turns that value into a safe list, skips the section if there are no notes, and for each note combines state, timing, document link, and body text. It returns one combined string for the rendered page.

**Call relations**: GoogleMeetConnector.render calls this after the transcript section. It uses _labeled for the note metadata and _str to safely read the optional body text.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 270–283)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns transcript entries into a clean conversation transcript. It groups consecutive text from the same speaker onto the same line so the output reads more naturally.

**Data flow**: It receives an unknown value that should contain transcript entries. It safely treats it as a list, skips entries without text, finds a speaker label for each entry, joins back-to-back entries from the same speaker, and returns newline-separated dialogue.

**Call relations**: _transcripts_section calls this when it needs the spoken part of a transcript. It uses _speaker to name each participant and _str to avoid formatting non-text values.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 286–299)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts plain text from the nested structure returned by the Google Docs API. It removes the document’s API wrapping and keeps only the actual text content.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the body content, walks through paragraph elements, collects text runs, joins their text together, trims extra whitespace at the ends, and returns the resulting string.

**Call relations**: _document_text calls this after successfully reading a smart-note Google Doc. This keeps the Google Docs format details isolated from the rest of the connector.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 302–309)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This extracts the Google Docs location attached to a transcript or smart note. It records both the document id and the export URL when Google provides them.

**Data flow**: It receives an artifact dictionary. If the artifact has a docsDestination object, it reads the document id and export link and returns them as docs_document and docs_url fields. If no valid destination exists, it returns an empty dictionary.

**Call relations**: _transcript and _smart_note call this while building artifact records. It uses _str so missing or non-text document fields become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 312–318)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest conference start time seen so far. It is used to move the sync cursor forward without losing track of already scanned meetings.

**Data flow**: It receives a list of conference dictionaries and the current cursor. It compares each conference’s startTime string with the current value and keeps the largest one. It returns the updated cursor, or the original cursor if nothing newer is found.

**Call relations**: paginate calls this after reading each page of conference records. The returned value becomes the next cursor in the StreamPage yielded to the sync system.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 321–323)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor one day into the past. It lets the connector re-check recently ended meetings because transcripts and AI notes may appear late.

**Data flow**: It receives a timestamp string. It parses it as a date-time, subtracts the configured one-day lookback, formats it back into Google’s expected timestamp style, and returns that string.

**Call relations**: paginate calls this when building the incremental Google Meet API filter. This makes the next API request include a small overlap with the previous sync window.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 326–327)

```
def _resource_id(name: str) -> str
```

**Purpose**: This pulls the final id segment out of a Google resource name. For example, it turns a long slash-separated API name into the short identifier at the end.

**Data flow**: It receives a resource name string. If the string is not empty, it splits on the last slash and returns the final piece; if it is empty, it returns an empty string.

**Call relations**: _conference_record, _transcript, _transcript_entries, _smart_note, and _speaker use this whenever they need a compact id or participant label from a Google resource name.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 330–332)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses a readable speaker label for a transcript entry. If Google gives a participant resource name, it uses the final id segment; otherwise it falls back to “Participant.”

**Data flow**: It receives a participant value from a transcript entry. It first makes sure the value is text, then extracts the resource id, and returns that id or the generic word “Participant” when no id is available.

**Call relations**: _dialogue calls this for every transcript entry it formats. It uses _str and _resource_id to turn raw participant data into a safe label.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 335–336)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only if it is already text. It prevents numbers, dictionaries, null values, or other unexpected data from appearing where the connector expects strings.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Many formatting and record-building functions call this before putting API values into ids, labels, titles, URLs, or rendered text. It is a simple guardrail used throughout the file.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 339–340)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats label-and-value pairs into simple lines such as “start: ...”. It leaves out blank values so the rendered page does not contain empty metadata rows.

**Data flow**: It receives a list of label and text-value pairs. It keeps only pairs whose value is not empty, formats each as one line, joins the lines with newline characters, and returns the result.

**Call relations**: GoogleMeetConnector.render, _transcripts_section, and _smart_notes_section call this when building metadata blocks for the final meeting page.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/providers/googlesheets.py`

`io_transport` · `source sync pagination and record rendering`

This connector is the bridge between UFO and Google Sheets. Its job is to take a Google account grant and turn three levels of spreadsheet data into sync records: the spreadsheet itself, each sheet tab inside it, and the cell values in each tab. Without this file, Google Sheets would not be available as a readable source.

The sync starts by asking Google Drive for spreadsheet files, ordered by their modified time. That modified time becomes the “watermark,” meaning the place where the next sync can safely resume. For each file, the connector asks the Google Sheets API for spreadsheet details and tab names. For cell contents, it asks for many tab grids at once in batches, then falls back to one-tab-at-a-time reads if Google refuses a batch.

A lot of this file is careful about partial failure. If the whole Google grant is missing a needed permission, the stream is skipped rather than pretending data was synced. If only one file or tab is refused, the connector records that refusal in the cursor so a later sync can retry it after permissions change. This is like leaving a sticky note on a skipped folder while still filing everything else.

Finally, the file includes small helpers that shape records and render them as readable text, such as listing tab names or formatting cell rows with separators.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 153–203)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs the main Google Sheets sync loop for one stream. It reads from the last saved cursor, fetches spreadsheet-related records page by page, and produces pages with the next cursor so the sync can resume later.

**Data flow**: It receives an HTTP client, a stream choice such as spreadsheets, sheets, or sheet_values, and an optional cursor from a previous run. It decodes that cursor into a watermark and any previously refused file ids, lists changed spreadsheets, turns each visit into records, updates the remembered refusal set, and yields StreamPage objects. If a previously refused file needs retrying, it fetches that file separately and emits a page for it. If Google says the whole grant lacks Drive or Sheets access, it turns that into a stream skip instead of ordinary records.

**Call relations**: This is the top-level driver used by the source framework when it wants records from Google Sheets. It asks _spreadsheet_visits for the normal Drive listing, _visit_records to convert each file visit into the requested stream's records, _carried_visit for retrying refused files, and the cursor helpers to remember where the run stopped.

*Call graph*: calls 7 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _settled); 2 external calls (__init__, refused_for_scope).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 205–230)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Lists spreadsheet files from Google Drive in pages. It applies the saved modified-time watermark so the connector mostly reads only files that changed since the last successful point.

**Data flow**: It receives an HTTP client and an optional watermark. It builds a Drive search query for non-trashed Google Sheets files, adds a modified-time filter when there is a watermark, follows Drive page tokens, and yields each non-empty batch of file metadata. It reads Drive's response and normalizes the files list so missing or null lists become empty lists.

**Call relations**: This is the Drive-listing worker beneath _spreadsheet_visits. _spreadsheet_visits consumes its batches and then fetches Sheets-specific details for each file.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 232–240)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: Turns Drive file listings into richer spreadsheet visits. A visit means “we looked at this spreadsheet id and either got a usable record or learned it was refused.”

**Data flow**: It receives an HTTP client and watermark, then reads batches from _iter_spreadsheet_files. For each file with a valid id, it calls _file_visit to combine Drive metadata with Sheets metadata, and yields the resulting _FileVisit object.

**Call relations**: paginate calls this during the normal listing phase. It sits between the broad Drive search and the per-file record-building work done by _file_visit.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 242–256)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: Retries a spreadsheet that was refused in an earlier sync. This lets the connector recover if permissions are later fixed, without blocking all other files in the meantime.

**Data flow**: It receives an HTTP client and a file id saved in the cursor. It asks Drive for current metadata, including whether the file is trashed. If Drive says the file is gone or still refused in a per-file way, it returns a visit with no record and an appropriate refusal flag. If the file is trashed, it clears the refusal. Otherwise, it passes the metadata to _file_visit to try the normal Sheets read again.

**Call relations**: paginate calls this after the ordinary Drive listing is drained, but only for carried refused ids that were not already seen in the listing. It uses _is_per_file_refusal to decide whether an error is safe to treat as one bad file rather than a whole-stream problem.

*Call graph*: calls 2 internal fn (_file_visit, _is_per_file_refusal); called by 1 (paginate); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._file_visit`  (lines 258–285)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: Builds the main spreadsheet record for one Drive file. It combines Drive information, such as timestamps and URL, with Sheets information, such as title and tab list.

**Data flow**: It receives an HTTP client, a file id, and Drive file metadata. It asks the Sheets API for spreadsheet metadata without cell grid data. If that request is refused only for this file, it creates a fallback record from Drive metadata alone and marks the visit refused. Otherwise, it returns a _FileVisit containing the combined spreadsheet record and whether access was partial.

**Call relations**: _spreadsheet_visits uses this for files found in the normal Drive listing, and _carried_visit uses it for previously refused files being retried. Downstream, _visit_records decides whether this spreadsheet record should be returned as-is or expanded into tabs or values.

*Call graph*: calls 1 internal fn (_is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._visit_records`  (lines 287–303)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: Converts one spreadsheet visit into records for the specific stream being synced. The same spreadsheet visit can become one spreadsheet record, many tab records, or many cell-value records.

**Data flow**: It receives an HTTP client, the requested stream, and a _FileVisit. If the visit has no record, it returns no records and passes along the refusal flag. For the spreadsheets stream it returns the spreadsheet record. For the sheets stream it expands tabs with _sheet_records. For the sheet_values stream it calls _sheet_value_records to read cell grids. It also returns whether any part of the file was refused.

**Call relations**: paginate calls this for both normally listed files and carried retry files. It is the dispatch point that connects the shared file-visiting flow to the three different Google Sheets streams.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 305–361)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: Reads the cell rows for each tab in a spreadsheet and shapes them into value records. It uses batch reads for efficiency, but can fall back to individual tab reads to isolate a refused tab.

**Data flow**: It receives an HTTP client and a spreadsheet record that already includes tab metadata. It collects valid tab titles and ids, asks the Sheets API for up to a fixed number of tab ranges at once, checks that Google returned one answer per requested tab, and turns each answer into a record. If a batch is refused for a per-file or per-tab reason, it retries each tab separately and skips only the tabs still refused. It returns the value records plus a flag saying whether any tab was refused.

**Call relations**: _visit_records calls this only for the sheet_values stream. It relies on _quoted_sheet_range to name tabs safely, _sheet_value_record to build final records, and _is_per_file_refusal to separate recoverable file/tab access failures from serious run failures.

*Call graph*: calls 4 internal fn (__init__, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 3 external calls (list_or_empty, error_detail, quote).


##### `GoogleSheetsConnector.render`  (lines 363–382)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced Google Sheets record into a readable title and text body. This is what makes spreadsheet data understandable when shown or indexed as recallable content.

**Data flow**: It receives one record and its stream description. For spreadsheet records, it builds text listing the tab names. For sheet records, it names the parent spreadsheet. For value records, it formats the grid rows as plain text. It returns a display title and a Markdown-like body headed with the connector and stream name.

**Call relations**: The broader source system calls render when it needs human-readable content from stored records. This function uses _str to safely read text fields and _grid_text to turn cell arrays into lines.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 385–398)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: Reads the saved sync cursor into the pieces this connector understands. The cursor may be a simple old-style watermark or a JSON checkpoint with refused file ids.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns empty starting state. If the cursor is not JSON or is not an object, it treats it as a plain watermark for backward compatibility. If it is a JSON checkpoint, it validates the shape and returns the watermark, refused ids, and the last retried id.

**Call relations**: paginate calls this at the start of a sync run. Its output drives both the Drive modified-time filter and the later retry pass for files that were refused before.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 401–406)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: Writes the connector's current progress back into a cursor string. It stores a simple watermark when possible, and a richer checkpoint when there are refused files to retry.

**Data flow**: It receives the current watermark, the set of refused file ids, and the last carried id retried. If there is no watermark or no refused files, it returns just the watermark. Otherwise, it serializes a checkpoint containing the watermark, a sorted limited list of refused ids, and the retry position when present.

**Call relations**: paginate calls this whenever it yields a page or final checkpoint. The saved value is later read by _decode_cursor on the next run.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 409–410)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: Updates the set of file ids that still need permission retry. It either keeps/adds the file when it is still refused or removes it once the connector can read it or knows it is gone.

**Data flow**: It receives the current refused-id set, one file id, and a yes/no flag for whether that file is still refused. If still refused, the returned set includes the id. If not, the returned set excludes it.

**Call relations**: paginate uses this after each normal visit and each carried retry. It is the small bookkeeping step that decides what the next cursor must remember.

*Call graph*: called by 1 (paginate).


##### `_is_per_file_refusal`  (lines 413–419)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: Decides whether a Google error should be treated as affecting only the requested file or tab. This matters because per-file failures can be skipped and retried later, while grant-wide or quota failures should stop or skip the stream.

**Data flow**: It receives an HTTP status code and parsed Google error details. It returns true only for 403 or 404 errors that include Google API details, are not quota refusals, and are not grant-wide permission problems. Otherwise it returns false.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records use this when Google rejects a metadata or value request. It calls _is_grant_refusal and Google's quota checker to avoid misclassifying broader access or usage-limit problems.

*Call graph*: calls 1 internal fn (_is_grant_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records); 1 external calls (is_quota_refusal).


##### `_is_grant_refusal`  (lines 422–427)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: Detects errors that mean the whole Google grant or API setup is missing required access. These are not just one bad spreadsheet; they mean the connector cannot safely read the stream.

**Data flow**: It receives parsed Google error details. It looks for known permission reasons such as missing API configuration or insufficient permissions, and also for Google service-level error details. It returns true when those signs appear, otherwise false.

**Call relations**: _is_per_file_refusal calls this as part of deciding whether an error can be treated as limited to one file. If this returns true, the error is not handled as a per-file fallback.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 430–451)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Expands one spreadsheet record into one record per sheet tab. This lets each tab be indexed or recalled separately from the whole spreadsheet.

**Data flow**: It receives a spreadsheet record containing a spreadsheet id, timestamps, title, and a Sheets API tab list. It skips malformed tab entries, copies each valid tab's metadata, adds a stable id made from spreadsheet id and sheet id, and stamps parent spreadsheet information onto the tab record. It returns the list of tab records.

**Call relations**: _visit_records calls this for the sheets stream. The records it creates are later yielded by paginate as that stream's page contents.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 454–467)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Builds one cell-value record for a single tab. It attaches the raw grid returned by Google to enough spreadsheet and tab information to identify where the values came from.

**Data flow**: It receives the parent spreadsheet record, a tab title, a sheet id, and a value-range response from Google. It copies the value-range fields, adds a stable id ending in ':values', adds spreadsheet and tab labels, and carries over created and updated timestamps. It returns the finished record dictionary.

**Call relations**: _sheet_value_records calls this after each successful batch or individual tab value read. The returned records become the sheet_values stream output.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 470–472)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: Formats a sheet tab title so the Google Sheets API reads it as a tab name. This avoids mistakes when titles contain spaces, punctuation, or apostrophes.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title, then wraps the whole title in apostrophes, producing a safe A1-style sheet reference. It returns that quoted range string.

**Call relations**: _sheet_value_records uses this before asking Google for tab values. Correct quoting is important because an unquoted name could be misread as a cell reference or named range.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 475–480)

```
def _grid_text(values: Any) -> str
```

**Purpose**: Turns a grid of cell values into plain readable lines. It is used when rendering sheet_values records for display or indexing.

**Data flow**: It receives any value. If the value is not a list, it returns an empty string. If it is a list of rows, it keeps rows that are lists, converts each cell to text, joins cells with ' | ', and joins rows with newlines.

**Call relations**: render calls this for the sheet_values stream. It is the final formatting step that makes raw cell arrays look like simple table text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 483–484)

```
def _str(value: Any) -> str
```

**Purpose**: Safely extracts a string value. It prevents non-text values from accidentally appearing in rendered titles and bodies.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: render uses this when building titles and body text for spreadsheet and sheet records. It keeps the rendered output clean even when source fields are missing or have unexpected types.

*Call graph*: called by 1 (render).

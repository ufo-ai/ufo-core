# Google and Microsoft source connectors  `stage-14.4`

This stage is the set of “adapters” that lets the sync system talk to Google and Microsoft services. It sits in the main data-gathering loop: each connector calls an outside web API, reads pages of results, and turns them into the project’s standard records so they can be stored, searched, updated, or deleted later.

On the Google side, Gmail reads mailbox changes and formats messages as text. Google Calendar reads events and attendee rows. Google Docs lists documents through Drive, fetches their content, and extracts plain text. Google Drive covers files, shared drives, permissions, comments, and revisions. Google Meet imports transcripts and generated notes. Google Sheets reads spreadsheets, tabs, and rows. Google Ads streams accounts, campaigns, ads, and statistics. The shared google.py helper tells connectors whether an access problem means “skip this” or “try again later.”

On the Microsoft side, Teams uses Microsoft Graph, Microsoft’s web API, to read teams, channels, chats, and messages. Outlook also uses Graph to sync mail, contacts, calendars, conversations, and folders.

## Files in this stage

### Google mail and access handling
Gmail ingestion is paired with shared Google error classification for permission and quota failures.

### `extensions/sources/ufo_ext_sources/providers/gmail.py`

`io_transport` · `source sync runs`

Gmail does not present an email as one simple text field. The useful parts are buried in a nested MIME tree, where plain text or HTML bodies are encoded, and sender, recipient, and subject live in headers. This file is the translator between Gmail’s API shape and the project’s normal stream of records.

The connector has two main jobs. First, it syncs mailbox changes. On the first run, it backfills messages inside a pinned time window, such as the last 30 days, and records Gmail’s history marker so later runs can ask only for changes since that point. On later runs, it uses Gmail history to find added and deleted messages. If Gmail says the saved marker is too old, it raises a cursor-expired signal so the wider system can start over cleanly. If the user’s grant lacks Gmail read permission, it marks the stream as skipped instead of treating the whole sync as broken.

Second, it makes each email readable. It fetches full message bodies, decodes Gmail’s URL-safe base64 text, extracts addresses and labels, and renders a clean page-like result with From, To, Cc, Subject, and body text. If only HTML is available, it strips tags and keeps readable text, like turning a styled web page into plain notes.

#### Function details

##### `GmailConnector.paginate_source`  (lines 94–104)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the entry point the broader source system uses when it wants Gmail records. It passes along the stream, saved cursor, and optional backfill cutoff, then delegates the real Gmail-specific paging work.

**Data flow**: It receives an HTTP client, a stream description, the saved Gmail cursor if there is one, and a possible backfill date. It ignores the self user id because Gmail here always uses the authenticated mailbox, then returns the pages produced by GmailConnector.paginate.

**Call relations**: The source runner calls this connector-level method during sync. It immediately hands the job to GmailConnector.paginate so all message listing, history walking, and body fetching happens in one place.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 106–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function produces pages of Gmail changes for the sync engine. It chooses between a first-time backfill and an ongoing change check, then packages new records and deletions into stream pages.

**Data flow**: It starts with a stream and either no cursor or a saved Gmail history id. With no cursor, it asks _backfill for message ids in the backfill window; with a cursor, it asks _history for added and deleted ids. It fetches full bodies for added messages in chunks and yields StreamPage objects containing records, deletion markers, and the next cursor. If Gmail refuses because the grant lacks read scope, it turns that into a skipped stream signal.

**Call relations**: GmailConnector.paginate_source calls this during a sync. It coordinates _backfill or _history, then calls _fetch_bodies before handing StreamPage results back to the core sync machinery.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 2 external calls (__init__, refused_for_scope).


##### `GmailConnector._backfill`  (lines 145–170)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This function finds the starting batch of Gmail messages when there is no saved cursor yet. It also chooses the history id that future runs should continue from, so the connector does not repeat the same backfill forever.

**Data flow**: It reads the mailbox profile history id first as a safe floor. It then lists message ids from Gmail, optionally using an after-date query converted to epoch seconds. After collecting all ids across Gmail pages, it asks _seed_history_id to choose the next history cursor and returns the collected ids plus that cursor.

**Call relations**: GmailConnector.paginate calls this only for cursor-less runs. It relies on _profile_history_id before listing and _seed_history_id afterward so later syncs can move from backfill mode into history-delta mode.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 172–175)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Gmail for the mailbox’s current history marker. That marker is used as a safe starting point when a backfill window contains no messages.

**Data flow**: It sends a profile request through the connector’s HTTP helper, reads the historyId field from Gmail’s response, and returns it if it is a string. If Gmail does not provide a usable value, it returns None.

**Call relations**: GmailConnector._backfill calls this before enumerating messages. The timing matters because it helps avoid skipping mail that arrives while an empty backfill window is being checked.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 177–201)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the cursor that the next sync run should use. It prefers the newest message’s Gmail history id, but falls back to the earlier mailbox profile marker if needed.

**Data flow**: It receives the ids found during backfill and the pre-read profile history id. If there are message ids, it fetches the first one in minimal form and tries to read its historyId. If that message disappeared with a 404, it ignores that and falls back to the floor. It returns the chosen history id or None.

**Call relations**: GmailConnector._backfill calls this after message listing. Its result is passed up to GmailConnector.paginate, which emits it as the next cursor for the core sync system.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 203–238)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log from a saved history id. It identifies which messages were added and which were deleted since the previous sync.

**Data flow**: It sends history.list requests starting from the saved id, follows Gmail page tokens, and gathers message ids from added and deleted sections. It keeps the latest historyId Gmail reports as the next cursor. If Gmail returns 404, meaning the old cursor aged out, it raises CursorExpired; otherwise it returns added ids with deletions removed, deleted ids, and the next cursor.

**Call relations**: GmailConnector.paginate calls this on normal incremental runs. It uses _message_ids to pull ids out of Gmail history entries, then hands the result back so paginate can fetch bodies for additions and emit deletion tombstones.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 240–254)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This fetches the full content for a list of Gmail message ids. It turns each raw Gmail response into the simpler record shape the rest of the system stores.

**Data flow**: It receives message ids, requests each message from Gmail in full format, skips any message that vanished with a 404, and raises other HTTP errors. For each successful response, it calls _flatten_message and returns the list of flattened records.

**Call relations**: GmailConnector.paginate calls this after it knows which messages are newly present. It hands raw Gmail message JSON to _flatten_message so rendering and storage do not have to understand Gmail’s nested payload format.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 256–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Gmail message record into readable prose. It makes an email look like something a person would recognize, with sender, recipients, subject, and body instead of raw API JSON.

**Data flow**: It receives a flattened record and stream description. For Gmail messages, it chooses a title from the subject or the default renderer, formats sender and recipients, adds the subject when present, chooses the best body text, and returns both the title and rendered text. For other streams, it falls back to the parent connector’s rendering.

**Call relations**: The wider source system calls render when it needs recallable text for a synced record. This method uses _str, _format_contact, _format_recipients, and _message_body to build the final human-readable page.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 279–288)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This helper pulls Gmail message ids out of history entries. It shields the rest of the code from Gmail’s nested added/deleted entry shape.

**Data flow**: It receives a value that should be a list of history entries. It walks through entries that are dictionaries, looks for entry.message.id, keeps only non-empty string ids, and returns a clean list of ids.

**Call relations**: GmailConnector._history calls this while reading added and deleted changes. The cleaned ids are then compared so messages that were both added and deleted are not fetched as live records.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 291–318)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Gmail message into a flat, project-friendly record. It extracts the parts people care about: ids, dates, subject, sender, recipients, labels, body text, and whether the message was sent or received.

**Data flow**: It receives Gmail’s raw message JSON. It reads selected headers, decodes plain-text and HTML bodies, parses the sender and recipients, filters label ids, and builds a dictionary with normalized fields. The output is easier to store, render, and search than Gmail’s original nested response.

**Call relations**: GmailConnector._fetch_bodies calls this for every full message fetched from Gmail. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 321–335)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This finds the first plain-text and first HTML body inside Gmail’s nested MIME payload. MIME is the email packaging format that lets one message contain multiple parts, such as text, HTML, and attachments.

**Data flow**: It receives the payload dictionary from Gmail. It walks through the payload tree and its child parts, decodes the first text/plain and text/html body data it finds, and returns a pair of optional strings: plain text and HTML.

**Call relations**: _flatten_message calls this when building a flat record. Its inner walk function does the tree traversal and calls _b64url_decode whenever it finds an encoded text body.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 325–332)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This inner helper recursively searches one MIME part and its children for readable email body content. Recursively means it can open nested parts inside nested parts, like looking through folders within folders.

**Data flow**: It receives one MIME part. If that part is plain text or HTML and has encoded body data, it decodes and stores it unless that type was already found. Then it visits each child part and repeats the same process.

**Call relations**: _extract_bodies starts the walk at the top-level Gmail payload. When body data is found, walk hands it to _b64url_decode so Gmail’s encoded string becomes normal text.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 338–344)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body encoding into normal text. Gmail uses URL-safe base64, a text-safe way to carry bytes, sometimes without the usual padding characters.

**Data flow**: It receives an encoded string, adds any missing padding, decodes it with URL-safe base64 rules, and converts the bytes to UTF-8 text while replacing invalid characters. If decoding fails, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this when it finds text/plain or text/html body data. The decoded text then becomes part of the flattened message record.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 347–354)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses a header such as From and returns the first email address it contains. It separates the display name from the actual email handle.

**Data flow**: It receives a header string or None. If there is no header or no parsed address, it returns two None values. Otherwise it lowercases the email address and returns it with the display name if one exists.

**Call relations**: _flatten_message calls this for the From header. It uses Python’s email address parser so quoted names and common email header formats are interpreted correctly.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 357–364)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses a recipient header such as To or Cc into a list of structured contacts. Each contact keeps the email address and optional display name.

**Data flow**: It receives a header string or None. With no header, it returns an empty list. Otherwise it parses all addresses, lowercases each email handle, drops entries without an address, and returns dictionaries for the remaining recipients.

**Call relations**: _flatten_message calls this for To and Cc headers. Later, GmailConnector.render uses the resulting contact lists when writing readable recipient lines.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 367–372)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable string. It writes either just the email address or a name plus address, like “Ada Lovelace <ada@example.com>”.

**Data flow**: It receives a possible email handle and display name. If the handle is not a non-empty string, it returns an empty string. If a display name is present, it combines name and handle; otherwise it returns the handle alone.

**Call relations**: GmailConnector.render uses this for the sender, and _format_recipients uses it for each recipient. It is the small formatting rule that keeps contact display consistent.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 375–382)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This turns a list of recipient contact records into one readable comma-separated line. It is used for To and Cc headers in the rendered email text.

**Data flow**: It receives a value that should be a list. If it is not a list, it returns an empty string. For each dictionary item, it formats the contact with _format_contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this while building the readable email header block. It delegates each individual contact’s appearance to _format_contact.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 385–393)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for an email. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail’s short snippet if no full body is available.

**Data flow**: It receives a flattened message record. If body_text is a non-empty string, it returns that trimmed text. Otherwise, if body_html is present, it calls _HtmlText.extract to strip tags and clean spacing. If neither body exists, it returns a trimmed snippet or an empty string.

**Call relations**: GmailConnector.render calls this when assembling the final message prose. It may hand HTML content to _HtmlText.extract so a message with no plain-text part is still readable.

*Call graph*: called by 1 (render).


##### `_str`  (lines 396–397)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a maybe-string value into a definite string. It prevents non-text values from accidentally being used as email subjects.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this when reading the subject. That lets render decide cleanly whether to use the subject or fall back to a default title.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 404–406)

```
def __init__(self) -> None
```

**Purpose**: This prepares a small HTML-to-text parser for one email body. It creates a place to collect pieces of readable text as the parser moves through the HTML.

**Data flow**: It receives no email content directly. It initializes the base HTML parser with character reference conversion enabled and starts an empty list of text parts. The parser object is then ready to receive HTML.

**Call relations**: _HtmlText.extract creates a new parser through this constructor. After setup, Python’s HTML parsing machinery calls handle_data, handle_starttag, and handle_endtag as it reads the HTML.


##### `_HtmlText.extract`  (lines 409–414)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It removes tags and attributes while preserving useful line breaks around block-like elements.

**Data flow**: It receives raw HTML. It creates a parser, feeds the HTML into it, joins the collected text pieces, normalizes repeated whitespace on each line, removes blank lines, and returns the cleaned text.

**Call relations**: _message_body calls this when an email has HTML but no usable plain-text body. The parser’s other methods collect text and line breaks during the feed step.


##### `_HtmlText.handle_data`  (lines 416–417)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records the visible text found inside an HTML email. It ignores tag structure and keeps the words a reader would see.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that chunk to the parser’s internal parts list. It returns nothing, but changes the parser’s collected output.

**Call relations**: Python’s HTMLParser calls this while _HtmlText.extract feeds HTML. The text it stores is later joined and cleaned by _HtmlText.extract.


##### `_HtmlText.handle_starttag`  (lines 419–421)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag represents a block boundary, such as a paragraph or table row. That keeps the final plain text from running unrelated sections together.

**Data flow**: It receives an HTML tag name and its attributes. If the tag is in the block-tag set, it appends a newline marker to the collected parts; otherwise it does nothing. Attributes are ignored because the goal is plain text.

**Call relations**: Python’s HTMLParser calls this during _HtmlText.extract. The inserted newlines work together with handle_endtag and handle_data to produce readable spacing.


##### `_HtmlText.handle_endtag`  (lines 423–425)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a closing HTML tag ends a block-like section. It helps preserve paragraph and layout separation in the plain-text version.

**Data flow**: It receives an HTML tag name. If the tag is one of the known block tags, it appends a newline marker to the collected parts. It returns nothing, but affects the text that extract later cleans and returns.

**Call relations**: Python’s HTMLParser calls this while _HtmlText.extract processes HTML. It complements handle_starttag so the stripped email still has sensible line breaks.


### `extensions/sources/ufo_ext_sources/providers/google.py`

`domain_logic` · `sync error handling`

Google APIs often report different problems with the same HTTP status codes, especially 401 and 403. In plain terms, both can look like “no,” but they can mean either “this account is not allowed to read that data” or “you have asked too often, try again later.” This file exists so the rest of the source-sync system does not treat those two cases the same.

That distinction matters. If a Google account truly lacks the needed permission, retrying will not help. The connector should skip that stream and move on. But if Google is only enforcing a usage quota, the right behavior is the opposite: fail the run in a way that allows normal backoff and retry, because the quota window may reset soon.

The file does this by looking inside the error response from Google. It first safely extracts the nested error details from the HTTP response body. Then it checks for known quota signals, such as Google’s RESOURCE_EXHAUSTED status or specific quota-related reason names. Finally, it answers the main question: is this 401 or 403 refusal really caused by missing permission, rather than quota? It is like reading the fine print on a rejection letter before deciding whether to give up or come back tomorrow.

#### Function details

##### `error_detail`  (lines 30–37)

```
def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This function pulls out the useful Google error information from an HTTP error response. If the response is not valid JSON, or does not contain the expected error object, it safely returns an empty dictionary instead of crashing.

**Data flow**: It receives an httpx.HTTPStatusError, which includes the server response. It tries to read the response body as JSON, then looks for the nested "error" field and makes sure it is a dictionary. The result is either that error-detail dictionary or an empty dictionary when there is nothing reliable to read.

**Call relations**: The main decision function, refused_for_scope, calls this first when it needs to inspect a Google refusal. error_detail relies on dict_or_empty to avoid being fooled by missing or wrongly shaped data in the response body.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (dict_or_empty).


##### `is_quota_refusal`  (lines 40–44)

```
def is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This function decides whether a Google refusal is really about a usage limit rather than account permission. It looks for Google’s quota markers, such as RESOURCE_EXHAUSTED or known quota reason names.

**Data flow**: It receives a dictionary of Google error details. It checks the top-level status value, then checks the list of individual error entries for quota-related reason strings. It returns True when the refusal points to quota exhaustion, and False otherwise.

**Call relations**: refused_for_scope calls this after extracting the error details. is_quota_refusal uses list_or_empty so it can safely examine the "errors" list even when Google leaves it out or sends it in an unexpected shape.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (list_or_empty).


##### `refused_for_scope`  (lines 47–52)

```
def refused_for_scope(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This is the main helper in the file. It answers whether a Google HTTP error should be treated as a settled permission problem, meaning the stream cannot be read with the current grant and should be skipped.

**Data flow**: It receives an httpx.HTTPStatusError. It first checks whether the HTTP status code is one of the refusal codes, 401 or 403. If so, it extracts the Google error details and checks whether the refusal is actually quota-related. It returns True only for 401 or 403 errors that are not quota refusals; quota errors return False so they can be retried by higher-level error handling.

**Call relations**: This function ties the file together. It calls error_detail to read Google’s error body, then calls is_quota_refusal to rule out temporary usage-limit failures. Callers use its answer to decide whether to skip a stream or let the error continue upward into retry and backoff behavior.

*Call graph*: calls 2 internal fn (error_detail, is_quota_refusal).


### Google business and calendar data
These connectors stream structured Google Ads and Google Calendar records into the sync system.

### `extensions/sources/ufo_ext_sources/providers/googleads.py`

`io_transport` · `source sync`

Google Ads does not give this project a simple database table to read. Instead, it expects the connector to send special query strings, called GAQL queries, to Google’s HTTP API. This file is the adapter that knows which Google Ads queries to send, which customer accounts to ask about, and how to reshape Google’s nested replies into flatter records the rest of the system can understand.

The connector first builds an HTTP client with the normal OAuth credential supplied elsewhere, then adds a required Google Ads developer token from the environment. Without that token, Google Ads will not allow access, so the stream is skipped rather than crashing the whole sync. It can also add a login customer id when accounts are managed through a manager account.

For each stream, such as campaigns or campaign metrics, the connector lists all accessible customer ids, runs the right query against each customer, and stamps the customer id onto every returned row. This is like checking every mailbox an advertiser can access, then labeling each letter with the mailbox it came from.

Finally, the file flattens selected responses. Google returns objects nested under names like customer, campaign, segments, and metrics; the rest of the system needs stable top-level fields such as id, resource_name, date, clicks, and impressions.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token, which is a separate approval token Google requires in addition to OAuth login. If the token is missing, it marks the Google Ads stream as skipped so the wider sync can continue instead of failing hard.

**Data flow**: It reads environment variables named UFO_GOOGLE_ADS_DEVELOPER_TOKEN and GOOGLE_ADS_DEVELOPER_TOKEN. If one is present, it returns that token as text. If neither is present, it creates a StreamSkipped error explaining that OAuth alone is not enough.

**Call relations**: When the connector is creating its HTTP client, GoogleAdsConnector._make_client calls this function so every Google Ads request can carry the required developer-token header.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Google Ads. It starts with the normal client from the shared REST connector, then adds Google Ads-specific request headers that Google expects.

**Data flow**: It receives a base API URL and a credential supplied by the auth proxy. It creates the basic authorized client, adds the developer token from GoogleAdsConnector._developer_token, optionally reads a login customer id from the environment, removes dashes from that id, and places it in a header. It returns the ready-to-use HTTP client.

**Call relations**: This is part of the setup path before any Google Ads query is sent. It relies on GoogleAdsConnector._developer_token to supply the required Google Ads token, and the resulting client is then used by later paging and query functions.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The rest of the connector needs this list because Google Ads queries are run one customer account at a time.

**Data flow**: It receives an authorized HTTP client, calls Google’s accessible-customers endpoint, and looks for resource names shaped like customers/1234567890. It extracts just the numeric customer id from each valid name and returns a list of those ids.

**Call relations**: GoogleAdsConnector._query_each_customer calls this first, then uses the returned ids to decide which customer accounts should receive the requested Google Ads query.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one GAQL query against one Google Ads customer account and collects the rows from Google’s streamed response. It hides the response format so callers can think in terms of normal lists of records.

**Data flow**: It receives an HTTP client, a customer id, and a query string. It posts the query to the customer’s Google Ads searchStream endpoint, reads the JSON response, walks through the returned batches, pulls out dictionary-shaped result rows, and returns them as a list.

**Call relations**: GoogleAdsConnector._query_each_customer calls this after choosing a customer id. It supplies the actual query built by GoogleAdsConnector.paginate and returns raw Google Ads rows for that one customer.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It also labels each row with the customer id it came from, which is important when one OAuth credential can see many advertiser accounts.

**Data flow**: It receives an HTTP client and a query string. It first asks GoogleAdsConnector._customer_ids for the accessible accounts. For each id, it calls GoogleAdsConnector._search_stream, then, if rows are returned, adds customer_id to every row and yields that group of rows as a page.

**Call relations**: GoogleAdsConnector.paginate uses this as the common worker for all supported streams. Paginate chooses what to ask for; this function decides where to ask it and hands back pages of labeled results.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the main reader for Google Ads streams. Given a requested stream, it builds the right Google Ads query and yields pages of records for customers, campaigns, ad groups, ads, campaign metrics, or customer-client relationships.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the last synced position. Based on the stream name, it creates a GAQL query. For campaign metrics, it uses the cursor date if present, otherwise it defaults to roughly the last 90 days. It sends the query through GoogleAdsConnector._query_each_customer and yields each returned page. If the stream is unknown, or if Google refuses access with an authorization status, it raises StreamSkipped with a clear reason.

**Call relations**: This is the connector’s central paging hook used by the source sync framework. It does not talk to each account directly; instead it delegates the repeated per-customer work to GoogleAdsConnector._query_each_customer, and that lower layer calls the account-listing and search functions.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes selected Google Ads records into simpler records with important fields at the top level. That makes them easier for the sync system to key, compare, and store.

**Data flow**: It receives one raw record and the stream it belongs to. For customers, it pulls out a stable id and display name. For campaigns, it lifts fields like resource_name, name, status, and start date. For campaign metrics, it combines customer id, campaign id, and date into a unique metric id and lifts values such as impressions, clicks, and cost. For streams that do not need special reshaping, it returns the record unchanged.

**Call relations**: After GoogleAdsConnector.paginate has yielded raw pages from Google Ads, the source framework can call this function record by record before saving or indexing them. It uses dict_or_empty so missing or oddly shaped nested objects become harmless empty dictionaries instead of causing avoidable errors.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/googlecalendar.py`

`io_transport` · `during Google Calendar source sync runs`

This connector is the bridge between Google Calendar and the rest of the sync system. Without it, the project would not know how to ask Google for calendar events, how to keep up with only the changes since the last run, or how to turn Google's event format into the simpler shape the system stores.

It works like a careful mail sorter. On the first run, it asks Google for recent events from a lookback window. After that, it uses Google's sync token, which is like a bookmark saying "continue from here next time." Each page from Google may contain normal events, changed events, or cancelled events. Normal events become records to save. Cancelled events become delete markers, so old copies can be removed.

The file defines two views of the same Google data. The `calendar_events` stream stores one row per calendar event, including title, time, location, organizer, and a folded-in attendee list. The `event_attendees` stream breaks the same event apart into one row per invitee, including their role and reply status.

It also knows how to render an event into readable text, so a calendar item can be recalled as something like a small note: title, when, where, who, and description. If Google's saved sync bookmark expires, the connector tells the core to start fresh. If the user did not grant Calendar permission, it records the stream as skipped instead of treating the whole run as broken.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 51–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches Google Calendar events page by page and turns them into sync pages the rest of the system can store. It supports both event records and attendee records, and it keeps track of Google's cursor so future runs only fetch changes.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. If there is a cursor, it sends it to Google as a sync bookmark; if not, it asks for events from the last 90 days and includes deleted events. For each Google page, it separates live records from cancelled items, converts live items into the right record shape, and returns `StreamPage` objects containing records, deletes, and eventually the next cursor. If Google says the cursor is too old, it raises a cursor-expired signal; if Calendar access was not granted, it raises a stream-skipped signal.

**Call relations**: This is the main read loop called by the source syncing framework when it needs data from Google Calendar. It hands raw Google events to `_flatten_event` for the event stream or `_flatten_attendees` for the attendee stream, then wraps the results in `StreamPage` objects for the core sync machinery.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 5 external calls (__init__, __init__, now, timedelta, refused_for_scope).


##### `GoogleCalendarConnector.render`  (lines 111–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a stored calendar event into human-readable text for recall or display. It gives event records a useful title and body instead of leaving them as raw fields.

**Data flow**: It receives a record and the stream it came from. For `calendar_events`, it reads fields such as title, start time, end time, location, attendees, and description, then builds a plain text summary. For other streams, it falls back to the parent connector's default rendering. It returns a pair: the display title and the rendered text body.

**Call relations**: The recall or indexing layer calls this when it needs readable content from a synced record. It uses `_str` to safely turn a possibly missing or non-text title into a string before building the event summary.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 139–166)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the simpler event record shape used by this system. It keeps the important event details and folds attendee information into the event record.

**Data flow**: It receives one event object from Google's API. It reads fields like ID, creation time, update time, summary, description, location, start and end times, organizer, status, recurring-event IDs, and attendees. It normalizes attendee entries with `_attendee` and normalizes date/time fields with `_parse_when`. It returns one dictionary ready to be stored as a calendar event record.

**Call relations**: The pagination loop calls this for each non-cancelled event when syncing the `calendar_events` stream. It delegates small cleanup jobs to `_attendee` and `_parse_when` so the main event conversion stays focused on assembling the record.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 169–175)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Cleans up one attendee entry for use inside an event record. It standardizes the attendee's email address and response status.

**Data flow**: It receives one attendee object from Google's event data. It lowercases the attendee email to make it a stable handle, copies the display name, and translates Google's response wording into the system's response wording. It returns a small dictionary describing that attendee.

**Call relations**: `_flatten_event` calls this while building the attendee list embedded inside a calendar event record. It is the small formatter that keeps attendee details consistent across events.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 178–204)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one Google Calendar event into several attendee rows, one per invitee. This lets the system store relationships such as "this person was invited to this event" separately from the event itself.

**Data flow**: It receives one raw Google event. It finds the event ID and organizer, then loops through the event's attendee list. For each attendee with an email address, it creates a row with a unique ID made from the event ID and attendee handle, plus timestamps, event ID, role, display name, response, and whether the attendee is the calendar owner. It returns a list of attendee-row dictionaries.

**Call relations**: The pagination loop calls this when syncing the `event_attendees` stream. For each attendee row, it asks `_attendee_role` to decide whether that person is the organizer, a resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 207–214)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in an event. It turns Google's attendee flags into one simple role label.

**Data flow**: It receives one attendee object and a separate yes/no value saying whether that attendee matches the organizer. It checks organizer status first, then whether the attendee is a resource such as a room, then whether attendance is optional. It returns one role string: `organizer`, `resource`, `optional`, or `required`.

**Call relations**: `_flatten_attendees` calls this while creating one row per invitee. Its result becomes the attendee row's role field, which helps later code understand how each invitee relates to the event.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 217–226)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google's two different date formats into one timestamp-like string. Google uses one shape for timed events and another for all-day events, and this function hides that difference.

**Data flow**: It receives a start or end value from a Google event. If the value contains `dateTime`, it returns that text directly. If it contains an all-day `date`, it turns it into midnight in UTC, written as an ISO-style timestamp. If the input is missing or not in the expected shape, it returns nothing.

**Call relations**: `_flatten_event` calls this for both the start and end of an event. That means stored event records can use `starts_at` and `ends_at` without each later reader needing to understand Google's separate date formats.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 229–230)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents rendering code from accidentally treating missing or non-text values as event titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: `GoogleCalendarConnector.render` calls this when reading the event title. It is a tiny guardrail that keeps the rendered calendar text clean even when a record has an unexpected title value.

*Call graph*: called by 1 (render).


### Google Workspace content artifacts
These providers read document, drive, meeting, and spreadsheet content from Google Workspace APIs.

### `extensions/sources/ufo_ext_sources/providers/googledocs.py`

`io_transport` · `source sync`

Google Docs are not simple files you can download as one block of text. First, the system must ask Google Drive which Docs exist, then ask the Google Docs API for each document’s full structure. This file is the connector that performs that two-step job.

The main class, GoogleDocsConnector, reads only; it does not create or edit documents. It lists Google Docs that are not trashed, ordered by their last modified time. If the sync already ran before, it sends Google a cursor, which is a saved timestamp, so Google only returns documents changed after that point. This keeps repeat syncs small and fast.

For each Drive file, the connector fetches the matching document from the Docs API. If one document is listed but cannot be opened, for example because access was removed, the connector keeps going and records a small stub instead of failing the whole sync. But if Google refuses the whole listing because the account lacks permission, the stream is skipped with a clear reason.

Finally, the connector turns Google’s nested document structure into readable prose. It walks through paragraph text runs, like reading each line from a complicated outline, and joins them into plain text.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 51–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read loop for Google Docs. It gathers changed document files from Drive, fetches each document’s content, shapes the result into records the sync system understands, and yields those records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It asks _iter_doc_files for pages of Drive file listings, then uses each file id to call _document for the full Google Docs data. It combines the Docs response with Drive metadata such as title, URL, creation time, and modified time, then outputs lists of records in page-sized batches. If Google refuses access to the whole stream because of missing permission, it turns that failure into a StreamSkipped message; other errors still fail normally.

**Call relations**: During a sync, the framework calls this method to pull records for the documents stream. It relies on _iter_doc_files to discover which files exist and on _document to fetch each file’s body. If Google returns an authorization-style failure, it checks google.refused_for_scope so the run can skip this stream cleanly instead of crashing for a predictable permission problem.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files); 1 external calls (refused_for_scope).


##### `GoogleDocsConnector._document`  (lines 89–98)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This function fetches one Google Doc by its file id. It protects the larger sync from failing just because one listed document cannot be opened.

**Data flow**: It receives an HTTP client and a Google file id. It requests the full document from the Google Docs API and returns the document data. If Google says the document is forbidden or missing, it returns a minimal record containing only the document id, so the caller still has something to store; other HTTP errors are passed upward.

**Call relations**: GoogleDocsConnector.paginate calls this for every file found through Drive. Its result is folded into the final record that paginate yields. This function is the point where a Drive listing becomes actual document content.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 100–127)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists Google Docs through the Drive API, one Drive page at a time. It is responsible for finding only relevant files: Google Docs, not trashed, and newer than the saved cursor when one exists.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query, adds paging settings and shared-drive options, then repeatedly asks Drive for files. Each response’s files field is normalized into a list and yielded if non-empty. If Google provides a next page token, it uses that token to fetch the next page; when no token remains, it stops.

**Call relations**: GoogleDocsConnector.paginate uses this as its source of Drive file metadata. This function hands back batches of candidate document files, and paginate then fetches each one through _document. It uses list_or_empty so odd or missing API responses do not break the loop just because the files field is absent or not a list.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 129–134)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns one synced Google Docs record into a title and a readable text block. The sync system can use that output as human-facing prose, for example for search or recall.

**Data flow**: It receives a record and the stream description. It reads the record’s title if it is a string, asks _plain_text to extract the body text, builds a heading that names the provider and stream, and returns both the title and the final formatted text.

**Call relations**: After records have been collected, the source framework can call render when it needs a plain-text version of a document. render delegates the document-body extraction to _plain_text, then wraps that extracted text with a simple heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 137–153)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This helper extracts readable text from Google’s nested document format. It focuses on paragraph text, which is where the normal written content of a Google Doc lives.

**Data flow**: It receives a document record. It looks inside body.content, walks through each paragraph, then through each paragraph element, and collects textRun.content strings. It joins all collected text in order, trims extra whitespace at the ends, and returns one plain string.

**Call relations**: GoogleDocsConnector.render calls this when it needs the body of a document as plain prose. It does not make network calls or change data; it simply translates Google’s structured document shape into text that the rest of the system can display or index.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledrive.py`

`io_transport` · `during Google Drive source sync`

This connector is the bridge between UFO and Google Drive. Without it, a user’s Drive files and related details would not become searchable or recallable metadata inside the system.

The file defines which Google Drive streams exist, such as files, shared drives, comments, permissions, and revisions. The main class, GoogleDriveConnector, knows how to fetch each stream from Google’s REST API, which is a web API reached through normal HTTP requests. It reads data only; it never writes back to Drive.

The most important stream is files. On the first run, the connector lists all non-trashed files and then asks Google for a “start page token,” which is like bookmarking the current end of the change log. On later runs, it uses that bookmark to fetch only changes: new or updated files become records, and removed or trashed files become delete notices. If Google says the bookmark is too old, the connector raises CursorExpired so the broader system can start fresh.

Other streams work differently. Shared drives are fully re-read each run. Permissions, comments, and revisions are fetched by first listing files and then visiting each file’s child collection. If the Google grant lacks Drive permission, the connector marks the stream as skipped instead of failing the whole sync.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 87–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Google Drive syncing. Given a stream name, it chooses the right way to fetch that kind of Drive data and yields pages of results for the sync system to consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. If the stream is files, it either reads the change log from the cursor or does a full file listing and saves a new change bookmark. If the stream is shared drives, it lists drives. If the stream is permissions, comments, or revisions, it walks through files and fetches those child records. It outputs lists of records or StreamPage objects that may include records, deletions, and the next cursor. If Google refuses because the account lacks Drive scope, it turns that refusal into a clean skip notice.

**Call relations**: The sync framework calls this when it wants pages for a Google Drive stream. This function then hands the work to _paginate_file_changes, _paginate_files, _start_page_token, _paginate_shared_drives, or _paginate_file_children depending on the stream. It also consults google.refused_for_scope when an HTTP error happens, so permission problems are reported as StreamSkipped rather than ordinary failures.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 2 external calls (__init__, refused_for_scope).


##### `GoogleDriveConnector._paginate_files`  (lines 121–146)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Google Drive file records in pages. It is used for the first full file sync and also as the file list that child streams use to find permissions, comments, and revisions.

**Data flow**: It receives an HTTP client and an optional time cursor. It builds a Drive search query for non-trashed files, and if a cursor is present it asks only for files modified after that time. It repeatedly calls Google’s files endpoint, converts a missing or non-list response into an empty list with list_or_empty, yields any records it finds, and follows Google’s next-page token until there are no more pages.

**Call relations**: paginate calls this when there is no saved files change-token and a full file listing is needed. _paginate_file_children also calls it so it can visit every file before asking Google for that file’s child records.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 148–153)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for a fresh change-log bookmark. The bookmark lets the next sync start from ‘what changed after this point’ instead of rereading every file.

**Data flow**: It receives an HTTP client, calls Google’s startPageToken endpoint, reads the startPageToken field from the response, and returns it only if it is a real non-empty string. Otherwise it returns None.

**Call relations**: paginate calls this after a full files sync. The returned token is wrapped in a StreamPage so the core sync system can save it as the cursor for the next run.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 155–200)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads Google Drive’s change log for files after a saved cursor. It is what makes later file syncs efficient, because it fetches only changed, deleted, or trashed files instead of listing everything again.

**Data flow**: It receives an HTTP client and a saved change-token cursor. It sends that token to Google’s changes endpoint, page by page. For each change, it separates active file records from deleted or trashed file IDs. It yields StreamPage objects containing updated records, delete IDs, and the next cursor. If Google returns status 410, meaning the saved token has expired, it raises CursorExpired so the system knows it must refetch from scratch.

**Call relations**: paginate calls this whenever the files stream already has a cursor. This function creates StreamPage results for the core sync process, and it uses list_or_empty to safely read Google’s changes list even if the response shape is not ideal.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 202–219)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists the shared drives visible to the connected Google account. Shared drives are re-read as a complete set each time rather than tracked through the file change log.

**Data flow**: It receives an HTTP client, calls Google’s shared drives endpoint, yields each non-empty page of drive records, and follows nextPageToken until Google says there are no more pages.

**Call relations**: paginate calls this when the requested stream is shared_drives. It uses list_or_empty to normalize the drives field before yielding records to the sync framework.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 221–259)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches per-file details such as permissions, comments, or revisions. It works like checking each folder tab on every file: first find the file, then ask Google for that file’s related records.

**Data flow**: It receives an HTTP client, a child stream description, and an optional cursor. It first lists all current files through _paginate_files. For each file with a valid ID, it calls the matching child endpoint, such as permissions or comments, page by page. It filters child records by the stream’s cursor field when a cursor is available, adds the parent file’s ID and name to each record, and yields non-empty batches. If Google returns 403 or 404 for a child collection, it skips that file’s child records and continues.

**Call relations**: paginate calls this for permissions, comments, and revisions. This function depends on _paginate_files to discover which files to inspect, and it uses list_or_empty to safely read each child collection from Google’s response.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 261–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a small human-readable text body. That text can be indexed or shown later as recallable metadata.

**Data flow**: It receives a record and its stream description. For non-file streams, it falls back to the base connector’s normal rendering. For file records, it reads the file name, MIME type, owners, and web link, then returns a title and a simple text block containing those details.

**Call relations**: The broader source framework calls render when it needs a readable representation of a synced record. This method uses _str to safely turn the file name into a string, and otherwise relies on the parent RestConnector rendering for streams other than files.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 279–280)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only if it is already a string. It prevents unexpected non-text values from being used as a file title.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. If not, it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when building the title for a Drive file. It keeps the rendering step simple and predictable even when Google’s record is missing a name or contains an unexpected value.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googlemeet.py`

`io_transport` · `source sync and record rendering`

Google Meet stores meeting history in several pieces: a conference record, optional transcript sessions, the individual transcript lines, and optional AI notes saved through Google Docs. This connector gathers those scattered pieces and rebuilds them into one useful page per meeting.

During a sync, it asks the Google Meet API for recent conference records, newest first. If the sync has run before, it does not start from the beginning every time. Instead, it uses the last seen meeting start time, with a one-day safety overlap, because transcripts and notes may appear after the meeting ends. That overlap is like checking yesterday’s mail again in case a late letter arrived.

For each conference, the file fetches transcript and smart-note artifacts. Transcript entries are copied into speaker-by-speaker dialogue. Smart notes may point to a Google Docs document; when possible, the connector also reads that document and extracts plain text. If the document is missing or access is denied, the sync keeps the link instead of failing.

The file is also careful about permissions. If Google refuses access because the account lacks the Meet scope, the stream is marked as skipped rather than treated as a broken system. Finally, the connector renders everything as simple Markdown-like text with labels, transcript sections, and AI summary sections.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 55–89)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main reader for the Google Meet stream. It pages through conference records from Google, fetches their transcripts and notes, and yields batches of meeting pages to the wider sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It builds a Google Meet API request, optionally adds a start-time filter with a safety lookback, reads conference pages, expands each conference into a full record, keeps only meetings with transcripts or smart notes, and outputs StreamPage objects with records plus the next cursor. If Google says the account lacks permission, it turns that into a skipped stream rather than a hard failure.

**Call relations**: The sync driver calls this when it wants Google Meet data. This function uses _lookback to widen an incremental sync window, _max_start_time to advance the saved cursor, _conference_record to build each meeting record, and Google permission helpers to decide whether an API refusal should skip the stream.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 3 external calls (__init__, list_or_empty, refused_for_scope).


##### `GoogleMeetConnector._conference_record`  (lines 91–114)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds one complete meeting record from a raw Google Meet conference. It gathers the meeting’s transcript artifacts and AI note artifacts so the rest of the system can treat the meeting as one object.

**Data flow**: It receives an HTTP client and one conference dictionary from Google. It reads the conference name, fetches transcript and smart-note artifact lists, expands each artifact into a richer dictionary, and returns a single record containing meeting times, space information, transcripts, and smart notes.

**Call relations**: GoogleMeetConnector.paginate calls this for every conference returned by Google. It delegates artifact listing to _artifacts, transcript shaping to _transcript, smart-note shaping to _smart_note, and small string cleanup to _str and _resource_id.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 116–133)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind for a conference, such as transcript sessions or smart-note sessions. It hides Google’s page-by-page API layout from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference resource name, and a collection name. If there is no parent name, it returns an empty list. Otherwise it repeatedly asks Google for artifact pages, gathers the artifact items from each response, follows next-page tokens, and returns one combined list.

**Call relations**: GoogleMeetConnector._conference_record calls this twice for each meeting: once for transcripts and once for smart notes. It passes the collected artifact lists back so _conference_record can expand them into full records.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 135–147)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google into the connector’s internal transcript shape. It includes the transcript’s metadata, its Google Docs destination, and its spoken entries.

**Data flow**: It receives an HTTP client and a transcript dictionary. It extracts the transcript name and timing fields, pulls out any linked Google Docs information, fetches transcript entries, and returns a dictionary ready to be attached to a meeting record.

**Call relations**: GoogleMeetConnector._conference_record calls this for every transcript artifact it finds. This function calls _transcript_entries to get the actual spoken lines and _docs_destination to preserve the durable document link.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 149–182)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual spoken lines inside a transcript session. These entries are what later become the readable conversation in the rendered page.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty, it returns an empty list. Otherwise it pages through Google’s transcript-entry endpoint, converts each entry into a simpler dictionary with speaker, text, language, and timing fields, and returns the full list. If Google says the related document or artifact is gone or unavailable, it returns whatever entries it already collected instead of crashing.

**Call relations**: GoogleMeetConnector._transcript calls this while building a transcript record. Later, the render path sends these entries through _transcripts_section and _dialogue so they become human-readable lines.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 184–199)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a record the system can store and render. When possible, it also reads the linked Google Docs file so the summary text is included directly.

**Data flow**: It receives an HTTP client and a smart-note dictionary. It extracts the note’s identity, state, timing, and document link. If there is a Google Docs document id, it asks _document_text for the document’s plain text and adds that text as the note body when available. It returns the completed note record.

**Call relations**: GoogleMeetConnector._conference_record calls this for every smart-note artifact. This function relies on _docs_destination for the document link and _document_text for optional inline summary text.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 201–210)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a Google Docs document and reduces it to plain text. It is used so Gemini meeting notes can be embedded in the meeting page instead of only linked.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely places that id into a Docs API URL, fetches the document, and passes the returned document structure to _plain_text. If Google says the file is missing or not readable, it returns an empty string; other errors are allowed to fail normally.

**Call relations**: GoogleMeetConnector._smart_note calls this when a smart note points to a Docs document. This function hands the raw Docs response to _plain_text, which performs the document-to-text extraction.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 212–229)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a stored Google Meet meeting record into a readable text page. It gives the page a title, meeting metadata, transcript text, and AI summary text.

**Data flow**: It receives a record and a stream description. For the Google Meet meeting-artifacts stream, it reads the record’s title, conference fields, transcripts, and smart notes, formats each part as text, joins the non-empty parts, and returns a title plus body. For any other stream, it falls back to the parent connector’s rendering behavior.

**Call relations**: The wider source system calls this after records have been fetched and need to become recallable prose. It calls _labeled for metadata blocks, _transcripts_section for transcript content, _smart_notes_section for summaries, and _str to avoid non-string surprises.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 232–248)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript sessions for a meeting into a readable section. It adds transcript metadata and the speaker-by-speaker dialogue.

**Data flow**: It receives any value that should contain transcript records. It turns missing or invalid input into an empty list, then for each transcript builds a small labeled metadata block and a dialogue block. It returns one joined text section, or an empty string if there are no transcripts.

**Call relations**: GoogleMeetConnector.render calls this while assembling the final meeting page. It calls _dialogue to turn transcript entries into conversation text and _labeled to format fields like state, start time, end time, and document link.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 251–267)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats all AI-generated meeting notes into a readable section. It preserves both the note metadata and the note body when the connector was able to read it.

**Data flow**: It receives any value that should contain smart-note records. It turns missing or invalid input into an empty list, then for each note builds labels for state, timing, and document link, adds the note body if present, and returns one joined text section. If there are no notes, it returns an empty string.

**Call relations**: GoogleMeetConnector.render calls this when building the meeting page. It uses _labeled for the note details and _str to safely read optional text fields.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 270–283)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into a simple conversation transcript. It also joins consecutive lines by the same speaker so the output is easier to read.

**Data flow**: It receives a value that should contain transcript entries. It skips empty text, converts each participant into a display name, and builds lines like “Participant: message”. If the same speaker continues in the next entry, it appends the text to the previous line instead of starting a new one. It returns the joined dialogue text.

**Call relations**: _transcripts_section calls this while formatting transcript content. It relies on _speaker to choose a readable speaker label and _str to ignore non-string transcript text.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 286–299)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. It strips away document layout details and keeps only text runs.

**Data flow**: It receives a Google Docs document dictionary. It walks through the body content, looks for paragraph elements, collects each text run’s content, joins the chunks, trims extra space at the ends, and returns the plain text.

**Call relations**: GoogleMeetConnector._document_text calls this after fetching a Google Docs document. This function is the last step that turns the Docs API response into text suitable for the smart-note body.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 302–309)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls Google Docs link information out of a Meet artifact. It keeps both the internal document id and the export URL when they are present.

**Data flow**: It receives a transcript or smart-note record from Google. If the record has a docsDestination object, it extracts the document id and export link as strings and returns them in a small dictionary. If there is no valid destination, it returns an empty dictionary.

**Call relations**: GoogleMeetConnector._transcript and GoogleMeetConnector._smart_note both call this while building artifact records. The returned document fields are later used for rendering links and, for smart notes, for fetching the document text.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 312–318)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This finds the newest meeting start time in a batch, while respecting the previously saved cursor. It helps the sync remember how far it has read.

**Data flow**: It receives a list of conference records and the current cursor. It compares each conference’s startTime string to the current value and keeps the largest one. It returns the updated cursor, or the original cursor if nothing newer was found.

**Call relations**: GoogleMeetConnector.paginate calls this after each Google Meet page is read. The result becomes the next cursor in the StreamPage so future syncs can continue from the latest known meeting time.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 321–323)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor one day backward. It creates a safety window so late-generated transcripts or smart notes are not missed.

**Data flow**: It receives an ISO-style timestamp string. It parses it as a date and time, subtracts the configured one-day lookback, formats the result back into Google-friendly timestamp text, and returns that string.

**Call relations**: GoogleMeetConnector.paginate calls this when an incremental sync has a cursor. The returned timestamp is placed into the Google Meet filter so the connector refetches a small recent window.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 326–327)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the final id from a Google resource name. Google often uses path-like names, and this helper keeps just the last part.

**Data flow**: It receives a resource name string such as a slash-separated API path. If the string is non-empty, it returns the part after the final slash; otherwise it returns an empty string.

**Call relations**: Several record-building functions call this when they need stable, short ids for conferences, transcripts, transcript entries, smart notes, and speakers. _speaker also uses it to turn participant resource names into display labels.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 330–332)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses the visible speaker name for a transcript entry. If Google provides a participant resource name, it uses the last part; otherwise it falls back to “Participant”.

**Data flow**: It receives any participant value from a transcript entry. It first keeps the value only if it is a string, then extracts the final resource id. If that produces nothing, it returns the generic label “Participant”.

**Call relations**: _dialogue calls this for each transcript entry that has text. The returned name is placed before the spoken words in the rendered transcript.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 335–336)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that accepts only real strings. It prevents unexpected non-string API values from leaking into rendered text or ids.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: This helper is used throughout the connector wherever Google data is turned into ids, labels, links, titles, or page text. It keeps the rest of the formatting code simple and predictable.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 339–340)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats a group of label-and-value pairs as simple text lines. Empty values are skipped so the final page does not show blank metadata fields.

**Data flow**: It receives a list of pairs such as label and value. It keeps only pairs with a non-empty value, formats each as “label: value”, joins them with newlines, and returns the resulting block of text.

**Call relations**: GoogleMeetConnector.render uses this for the meeting header, while _transcripts_section and _smart_notes_section use it for artifact details. It is the shared formatter for compact metadata blocks in the final page.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/providers/googlesheets.py`

`io_transport` · `source sync runs`

This connector is the bridge between UFO and Google Sheets. Without it, the system would not know how to find a user’s spreadsheets, split them into useful pieces, or keep track of what changed since the last sync.

The file reads Google Sheets in three layers. First it asks Google Drive for spreadsheet files, because Drive knows which files exist and when each was last changed. Then it asks the Sheets API for each spreadsheet’s details, including its tab names. Finally, for the values stream, it reads the cell grids from those tabs.

A key idea here is the cursor, also called a watermark: it is the saved “last seen modified time.” On the next run, the connector asks Drive only for files modified at or after that time. The “at or after” part is deliberate, because several files can share the same timestamp; re-reading tied files is safer than accidentally skipping one.

The file also has careful behavior for permission problems. If the whole Google grant is missing the right access, the stream is skipped. If only one spreadsheet or tab is refused, the connector records that file id and tries it again later, rather than letting one blocked file stop the whole sync. For rendering, it turns spreadsheet titles, tab names, and grid rows into readable text.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 152–202)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for each Google Sheets stream. It walks through changed spreadsheets, turns them into stream records, yields pages of records, and saves enough cursor information to resume safely later.

**Data flow**: It receives an HTTP client, a stream choice such as spreadsheets, sheets, or sheet_values, and the previous cursor. It decodes that cursor into a last-seen time and any refused file ids, lists spreadsheet files from Drive, converts each file into records, groups records into pages, updates the cursor, and yields StreamPage objects. If Google says the whole grant cannot read Drive or Sheets, it changes that failure into a stream skip instead of treating it like an ordinary crash.

**Call relations**: The sync framework calls this when it wants records from Google Sheets. It relies on _decode_cursor at the start, asks _spreadsheet_visits for files found through the normal Drive listing, calls _visit_records to make records for the selected stream, uses _settled to update the refused-file carry list, and uses _encode_cursor whenever it reports progress. After the normal listing, it calls _carried_visit for files that were refused before and need a retry.

*Call graph*: calls 7 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _settled); 2 external calls (__init__, refused_for_scope).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 204–229)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function pages through Google Drive’s file list and returns batches of spreadsheet files. It is the part that discovers which spreadsheets exist and which ones have changed since the saved watermark.

**Data flow**: It receives an HTTP client and an optional watermark time. It builds a Drive search query for untrashed Google Sheets files, adds a modified-time filter if there is a watermark, follows Drive page tokens, and yields each non-empty batch of file metadata. It reads Drive’s nextPageToken to know whether there is more to fetch.

**Call relations**: _spreadsheet_visits calls this to get the raw Drive files before turning each one into a fuller spreadsheet visit. It also uses list_or_empty so missing or oddly shaped file lists become a safe empty list rather than surprising the loop.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 231–239)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This function turns Drive file listings into spreadsheet visits. A visit is the connector’s small bundle saying: this file id was looked at, here is the spreadsheet record if available, and here is whether access was refused.

**Data flow**: It receives an HTTP client and watermark. It gets batches of spreadsheet files from _iter_spreadsheet_files, pulls out each valid file id, and asks _file_visit to fetch Sheets metadata and combine it with Drive metadata. It yields one _FileVisit per usable spreadsheet id.

**Call relations**: paginate calls this during the normal Drive-listing part of a sync. For each Drive file it finds, this function hands off to _file_visit so the connector can enrich the Drive metadata with Sheets-specific information such as tab list and spreadsheet title.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 241–255)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This retries one spreadsheet id that was refused in an earlier run. It lets the connector recover automatically if a user later grants access to a file that used to be blocked.

**Data flow**: It receives an HTTP client and a file id from the carried refusal list. It asks Drive for that file’s metadata, including whether it is trashed. If the file is gone or still refused in a per-file way, it returns a visit with no record and the right refused status. If the file is trashed, it clears the refusal. Otherwise, it passes the file metadata to _file_visit and returns the resulting visit.

**Call relations**: paginate calls this after finishing the normal Drive listing, but only for carried file ids that were not already seen in the listing. It uses _is_per_file_refusal and google.error_detail to decide whether an error is about this one file, and it hands readable files onward to _file_visit.

*Call graph*: calls 2 internal fn (_file_visit, _is_per_file_refusal); called by 1 (paginate); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._file_visit`  (lines 257–284)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This fetches the Sheets metadata for one spreadsheet and combines it with Drive metadata. If the Sheets metadata is refused for that one file, it still creates a minimal spreadsheet record from Drive so the sync can move on.

**Data flow**: It receives an HTTP client, a file id, and Drive’s file metadata. It requests the spreadsheet from the Sheets API without cell grid data. On success, it builds a record with ids, title, URL, creation time, modified time, and the Sheets metadata. On a per-file refusal, it marks the visit as refused and creates a fallback record using the Drive file name as the title.

**Call relations**: _spreadsheet_visits uses this for files found through Drive listing, and _carried_visit uses it for retried refused files. It calls _is_per_file_refusal, with details from google.error_detail, to distinguish “this one file is blocked” from broader failures that should stop or skip the stream.

*Call graph*: calls 1 internal fn (_is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._visit_records`  (lines 286–302)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This converts one spreadsheet visit into the specific kind of records requested by the current stream. It is the switchboard between spreadsheet-level records, tab-level records, and cell-value records.

**Data flow**: It receives an HTTP client, the selected stream, and a _FileVisit. If the visit has no record, it returns no records and preserves the refused flag. For the spreadsheets stream, it returns the spreadsheet record itself. For the sheets stream, it expands the spreadsheet into tab records. For the sheet_values stream, it asks _sheet_value_records to fetch rows from each tab. It returns both the produced records and whether anything was refused.

**Call relations**: paginate calls this for every visit, no matter whether it came from the main listing or carried retry path. It delegates tab creation to _sheet_records and grid fetching to _sheet_value_records, so paginate does not need to know the details of each stream shape.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 304–360)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This reads the actual cell rows from each tab in a spreadsheet. It groups tab reads into bounded batches for efficiency, but can fall back to reading tabs one by one if Google refuses a batch.

**Data flow**: It receives an HTTP client and a spreadsheet record that already contains tab metadata. It collects valid tab titles and sheet ids, asks the Sheets API for their values in chunks, checks that Google returned exactly one answer per requested tab, and turns each answer into a sheet value record. If a batch is refused for a per-file reason, it retries each tab individually, dropping only tabs that are still refused. It returns the value records plus a flag saying whether any tab was refused.

**Call relations**: _visit_records calls this when the active stream is sheet_values. It uses _quoted_sheet_range to name tabs safely for Google’s A1 range syntax, _sheet_value_record to package each grid as a record, _is_per_file_refusal and google.error_detail to classify refusals, list_or_empty to safely read Google responses, and StreamFault when Google’s batch response does not match the request.

*Call graph*: calls 4 internal fn (__init__, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 3 external calls (list_or_empty, error_detail, quote).


##### `GoogleSheetsConnector.render`  (lines 362–381)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns synced Google Sheets records into readable text for recall or display. It gives each stream a simple heading and body instead of exposing raw API data.

**Data flow**: It receives a record and its stream. For spreadsheet records, it builds text showing the spreadsheet title and tab names. For sheet records, it names the tab and its parent spreadsheet. For sheet value records, it converts rows into pipe-separated lines. It returns a short title and a longer text body.

**Call relations**: The broader source system calls render when it needs human-readable content from a synced record. This function uses _str to safely pull text fields and _grid_text to format cell rows, and falls back to the parent RestConnector rendering for unknown streams.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 384–397)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This reads the saved cursor from a previous sync and turns it into pieces the connector can use. It supports both old simple cursors and newer JSON checkpoints that also remember refused file ids.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns no watermark, no carried file ids, and no retried marker. If the cursor is plain text or not a checkpoint object, it treats it as the old watermark format. If it is a valid checkpoint JSON object, it returns the watermark, refused ids, and the last retried id. If the JSON looks like a checkpoint but does not match the expected shape, it raises an error.

**Call relations**: paginate calls this at the start of every run. The result controls where Drive listing begins and which previously refused files need a retry after the listing is drained.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 400–405)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This builds the cursor that will be saved after a page of work. It keeps the common case simple, but stores a JSON checkpoint when there are refused files to retry later.

**Data flow**: It receives the current watermark, the set of refused file ids, and the current retried marker. If there is no watermark or no refused ids, it returns just the watermark. If refused ids exist, it creates a checkpoint with the watermark, a sorted limited list of refused ids, and the retried marker if present, then turns that checkpoint into JSON.

**Call relations**: paginate calls this whenever it yields progress. The cursor it creates is later read by _decode_cursor, forming the connector’s memory between sync runs.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 408–409)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This updates the set of file ids that still need future retry. It is a tiny helper for saying whether a refused file remains unresolved or has now been successfully settled.

**Data flow**: It receives the current refused-id set, one file id, and a boolean saying whether that file is still refused. If still refused, it returns a set with that id included. If not, it returns a set with that id removed.

**Call relations**: paginate calls this after each listed or retried file is processed. Its result is passed into _encode_cursor so future runs retry only the files that remain blocked.

*Call graph*: called by 1 (paginate).


##### `_is_per_file_refusal`  (lines 412–418)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This decides whether a Google error is about one specific file rather than the whole Google grant or a quota problem. That distinction matters because one blocked file can be carried forward, while a broken grant or quota issue should not be hidden.

**Data flow**: It receives an HTTP status code and parsed Google error details. It returns true only for 403 or 404 errors that include Google API details, are not quota refusals, and are not grant-wide refusals. Otherwise it returns false.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this when Google returns an error. It calls google.is_quota_refusal and _is_grant_refusal to avoid misclassifying quota or missing-permission problems as a harmless single-file refusal.

*Call graph*: calls 1 internal fn (_is_grant_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records); 1 external calls (is_quota_refusal).


##### `_is_grant_refusal`  (lines 421–426)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects errors that mean the user’s Google connection or the project’s API setup lacks the needed access. These are broad failures, not problems with just one spreadsheet.

**Data flow**: It receives parsed Google error details. It checks the error list for known permission/setup reasons and checks detailed error entries for Google’s service-wide error domain. It returns true when the error points to missing Drive or Sheets access for the grant or service.

**Call relations**: _is_per_file_refusal calls this as part of its classification. This helper uses list_or_empty so absent error arrays are treated as empty lists instead of causing another error.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 429–450)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This expands one spreadsheet record into one record per sheet tab. It lets the system sync and recall individual tabs, not just whole spreadsheets.

**Data flow**: It receives a spreadsheet record. It reads the spreadsheet id, loops through the spreadsheet’s sheet list, ignores malformed entries, and for each tab with a sheet id creates a record containing the tab data, a stable id, parent spreadsheet information, title, and timestamps. It returns the list of tab records.

**Call relations**: _visit_records calls this when the selected stream is sheets. It does not fetch anything itself; it reshapes metadata that _file_visit already collected.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 453–466)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This packages one tab’s cell values into the record shape used by the sheet_values stream. It adds parent spreadsheet and tab identity around Google’s returned value range.

**Data flow**: It receives the parent spreadsheet record, the tab title, the tab id, and the value range returned by Google. It combines those into a new record with a stable id, spreadsheet id and title, sheet id and title, timestamps, and the original value data. It returns that record.

**Call relations**: _sheet_value_records calls this after each successful batch or individual tab read. This helper keeps the record shape consistent regardless of how the tab values were fetched.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 469–471)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This turns a sheet tab title into a safe Google Sheets range name. It is important because tab names can contain spaces, punctuation, or apostrophes that Google would otherwise misread.

**Data flow**: It receives a tab title. It doubles any apostrophes inside the title and wraps the whole title in single quotes, producing a quoted A1-style sheet reference. It returns that quoted range string.

**Call relations**: _sheet_value_records calls this when building values:batchGet requests and individual tab URLs. The quoting is what makes Google interpret the text as a tab name rather than a cell address or named range.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 474–479)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This formats a tab’s cell grid into simple readable text. It makes rows look like plain lines, with cells separated by vertical bars.

**Data flow**: It receives any value. If the value is not a list, it returns an empty string. If it is a list, it keeps rows that are also lists, converts each cell to text, joins cells with ` | `, then joins rows with newlines. The output is a plain text version of the grid.

**Call relations**: GoogleSheetsConnector.render calls this for sheet_values records. It is the final formatting step that turns raw row arrays from Google into something useful for human reading.

*Call graph*: called by 1 (render).


##### `_str`  (lines 482–483)

```
def _str(value: Any) -> str
```

**Purpose**: This safely extracts a string value for rendering. It avoids accidentally printing Python objects or `None` where a clean title is expected.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: GoogleSheetsConnector.render calls this whenever it needs a title or label from a record. It keeps the rendered headings and bodies tidy even when Google data is missing or has an unexpected type.

*Call graph*: called by 1 (render).


### Microsoft Graph connectors
Microsoft Teams and Outlook providers use Graph APIs to stream collaboration, mail, contact, calendar, and folder data.

### `extensions/sources/ufo_ext_sources/providers/microsoft_teams.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Microsoft Teams. Without it, the system would not know where to ask Microsoft for Teams content, how to walk through Teams’ paged responses, or how to turn Teams messages into readable text.

The file defines several streams, which are categories of data to sync: joined teams, channels inside those teams, messages inside channels, chats, and messages inside chats. It uses Microsoft Graph, where large lists come back one page at a time. Think of it like reading a long photo album: the connector opens one page, copies the entries, then follows Microsoft’s “next page” link until there are no more.

The connector starts from what the signed-in user can see: their joined teams and chats. For each team, it asks for channels. For each channel or chat, it asks for messages. Message streams support incremental syncing: if the system already saved a previous “last modified” timestamp, the connector only yields messages changed after that point.

It is careful about permissions. If one team, channel, or chat cannot be read, it skips that parent and continues with the others. But if Microsoft refuses the whole stream because the user grant lacks permission, the connector reports the stream as skipped instead of crashing the entire run. Message bodies arrive as HTML, so the render step strips tags to make a plain, readable page.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Microsoft Teams that the signed-in user has joined. This is the starting point for finding team channels and channel messages.

**Data flow**: It receives an HTTP client that already knows how to talk to Microsoft Graph. It asks the `/me/joinedTeams` endpoint for teams, follows all pages of results, collects every team record into one list, and returns that list.

**Call relations**: When the connector needs teams directly, `MicrosoftTeamsConnector.paginate` calls this function. When it needs channels, `MicrosoftTeamsConnector._channels` first calls this function so it knows which teams to inspect.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches channels for each team the user belongs to. It adds team information to each channel record so later steps know which team the channel came from.

**Data flow**: It starts by reading the user’s teams through `_teams`. For each team with a usable ID, it asks Microsoft Graph for that team’s channels. Each batch of channels is enriched with context such as `team_id` and `team_name`, then yielded onward. If a single team is forbidden or missing, it skips that team and keeps going.

**Call relations**: This function sits between team discovery and message discovery. `MicrosoftTeamsConnector.paginate` calls it when syncing the channel stream, and `MicrosoftTeamsConnector._channel_messages` calls it when it needs channels before asking for channel messages. It uses `with_context` to attach the parent team details that downstream code needs.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every readable Teams channel. It can limit results to messages modified after the last saved sync point, which avoids reprocessing old content.

**Data flow**: It receives an HTTP client and an optional cursor, which is the previous watermark timestamp. It gets channel records from `_channels`, uses each channel’s team ID and channel ID to request messages, filters out messages whose `lastModifiedDateTime` is not newer than the cursor, adds channel and thread context, and yields only non-empty batches. If one channel cannot be read or no longer exists, it skips that channel.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the system asks for the `channel_messages` stream. This function depends on `_channels` for the list of places to look, then uses `with_context` so the saved messages still carry their team, channel, and thread identity.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the one-to-one and group chats visible to the signed-in user. This is the starting point for syncing chat messages.

**Data flow**: It receives an HTTP client, requests `/me/chats` from Microsoft Graph, follows every page of results, collects all chat records into one list, and returns that list.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when syncing chat records. `MicrosoftTeamsConnector._chat_messages` calls it first so it knows which chats to request messages from.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from each readable Microsoft Teams chat. Like channel messages, it supports incremental syncing so the system can focus on changed messages.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It first gets all chats from `_chats`, then asks Microsoft Graph for messages in each chat with a valid chat ID. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is newer. It adds chat and thread context to each remaining batch and yields it. If a particular chat is forbidden or missing, it skips that chat.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this for the `chat_messages` stream. It relies on `_chats` to find the chats and uses `with_context` to preserve which chat each message came from.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the connector’s dispatcher for reading each Teams stream. Given a requested stream, it chooses the right helper and yields pages of records in the format the sync engine expects.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper for teams, channels, channel messages, chats, or chat messages, and yields each page it receives. If Microsoft refuses access to an entire stream with an authorization error, it turns that into `StreamSkipped` so the run records a permission skip rather than a hard failure. If the stream name is unknown, it also reports it as skipped.

**Call relations**: The broader sync framework calls this function whenever it wants records from this connector. `paginate` then hands off to `_teams`, `_channels`, `_channel_messages`, `_chats`, or `_chat_messages` depending on the stream. It is the main traffic director for all reading done by this file.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into a title and readable page text. For messages, it strips Microsoft’s HTML body into plain text so search and recall show human-friendly content.

**Data flow**: It receives one record and the stream it belongs to. For ordinary structural streams such as teams and channels, it lets the base connector render them. For channel and chat messages, it reads the subject, pulls `body.content` from the nested record, removes HTML tags, builds a simple heading, and returns the title plus page body.

**Call relations**: The sync system calls this after records are fetched and before they are stored as recallable pages. This function uses `_str` to safely read the subject, `get_path` to reach the nested message body, and `_strip_html` to make the message readable.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a small piece of HTML text into plain text by removing tags. This matters because Microsoft Graph stores Teams message bodies as HTML, but the recall page should be readable text.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces anything that looks like an HTML tag with a space, trims leading and trailing whitespace, and returns the cleaned text.

**Call relations**: `MicrosoftTeamsConnector.render` calls this while preparing channel and chat messages for storage. It is a small helper used only at the final presentation step, after records have already been fetched.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into a usable string only when it is already a string. It prevents non-text values from accidentally becoming confusing titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this to read a message subject before building the displayed title and heading.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/outlook.py`

`io_transport` · `source sync runs`

This connector is the bridge between the project and a user’s Outlook account. Without it, the system would not know which Microsoft Graph web addresses to call, how to page through results, or how to remember the “bookmark” that says where the last sync stopped.

Microsoft Graph exposes most Outlook data through “delta” endpoints. A delta endpoint is like asking, “Show me everything the first time, then next time only show what changed.” This file stores Graph’s returned delta links as cursors, which are bookmarks for later runs. Messages and contacts are trickier because they live inside folders, so their cursor is a small JSON map from folder ID to that folder’s bookmark.

The main class, OutlookConnector, declares the available streams and routes each stream to the right reading method. Mail messages, contacts, calendar events, and mail folders use Graph delta feeds. Conversations are not a separate Outlook object here; they are built by reading messages and grouping them by conversationId.

The file also reshapes records into easier fields, such as contact email, message sender, event title, and plain-text event description. If Microsoft refuses access with a 401 or 403 error, the connector marks the stream as skipped instead of crashing the whole sync, because that usually means the account grant lacks the needed permission.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: Formats a Python date and time into the UTC timestamp style Microsoft Graph expects in filters. This keeps time comparisons clear and consistent when asking Graph for data after a certain point.

**Data flow**: It receives a datetime value, converts it to UTC, and turns it into a string like 2024-01-01T12:00:00Z. The output is used inside Microsoft Graph query filters.

**Call relations**: The conversation and message readers call this when they need to add a first-sync backfill floor. It supplies the correctly formatted time before those readers ask Microsoft Graph for filtered data.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes HTML tags from a text value, mainly so calendar event descriptions become readable plain text. If the input is not text, it safely returns nothing.

**Data flow**: It receives any value. If the value is a string, it replaces HTML tags with spaces and trims the result; otherwise it returns null. The caller gets a cleaner description field.

**Call relations**: OutlookConnector.flatten calls this while preparing event records. It is the small cleanup step between Graph’s HTML event body and the simpler text field the rest of the system can display or index.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact. Contacts can contain several email entries, so this chooses a practical single email field for the normalized record.

**Data flow**: It receives a contact record, looks at its emailAddresses list, and reads each nested emailAddress.address value. It returns the first non-empty address it finds, or null if none is usable.

**Call relations**: OutlookConnector.flatten calls this when turning a raw contact into a friendlier contact shape. It relies on the shared get_path helper to read nested data without fragile manual indexing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from an Outlook contact. It prefers the mobile phone, then falls back to the first business phone.

**Data flow**: It receives a contact record, checks mobilePhone first, then scans businessPhones if needed. It returns one phone string or null when no suitable phone number is present.

**Call relations**: OutlookConnector.flatten calls this during contact normalization. It turns Outlook’s multiple phone fields into one simple phone value for downstream use.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the connector’s public paging entry for the source runner. It accepts the runner’s standard inputs and forwards them to the Outlook-specific pagination logic, including the optional backfill date.

**Data flow**: It receives an HTTP client, a stream description, an optional cursor bookmark, an optional user ID, and an optional backfill floor. It passes the meaningful pieces into paginate and yields whatever pages paginate produces.

**Call relations**: The broader source framework calls this when it wants records for one Outlook stream. This method then hands control to OutlookConnector.paginate, which decides the correct stream-specific path.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right reader for each Outlook stream and turns permission problems into clean stream skips. This is the central traffic director for contacts, messages, conversations, events, and mail folders.

**Data flow**: It receives a stream name plus cursor and backfill information. Based on the stream, it yields pages from the matching helper. If Microsoft Graph returns 401 or 403, it raises StreamSkipped so the sync records a skipped stream instead of treating it as a broken run.

**Call relations**: OutlookConnector.paginate_source hands work to this function. It then delegates to _conversation_pages, _message_delta_pages, _contact_delta_pages, _event_delta_pages, or _graph_delta_pages depending on what the runner requested.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records by reading Outlook messages and grouping them by conversationId. Outlook does not supply this connector’s conversation stream directly, so this function derives it from mail.

**Data flow**: It receives an HTTP client, an optional cursor, and an optional first-sync cutoff date. It asks for messages ordered by last modified time, groups them by conversation ID, keeps the earliest creation time and latest update per conversation, and finally yields a list of conversation records.

**Call relations**: OutlookConnector.paginate calls this for the conversations stream. When a backfill date is needed, it calls _graph_instant to format that date for Microsoft Graph before reading messages.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta feed and converts each response into the project’s StreamPage format. It understands both new or changed records and deleted records.

**Data flow**: It starts with either a saved cursor link or an initial Graph path plus optional query parameters. For each Graph response, it separates normal items from items marked @removed, picks up the next or delta link as the next cursor, and yields a StreamPage containing records, delete IDs, and the bookmark for the next run.

**Call relations**: This is the shared delta-feed worker. OutlookConnector.paginate uses it directly for mail folders, while the message, contact, and event helpers call it as their lower-level reader.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook messages from every mail folder and keeps a separate cursor for each folder. This matters because Microsoft Graph message deltas are folder-based, not one single mailbox-wide feed here.

**Data flow**: It receives an optional JSON cursor map and optional backfill date. It decodes the cursor map, lists mail folders, reads each folder’s message delta feed, adds the folder ID onto each message record, updates that folder’s cursor, and yields StreamPage objects with the refreshed cursor map encoded as JSON.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It asks _list_mail_folders for the folder IDs, uses _graph_delta_pages to read each folder, and uses _decode_cursor_map and _encode_cursor_map to keep the per-folder bookmarks intact.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook contacts from the default contacts area and any extra contact folders. Like messages, contacts need folder-by-folder bookmarks.

**Data flow**: It receives an optional JSON cursor map. It decodes that map, builds a folder list starting with the default contacts area, reads each folder’s contact delta feed, updates the matching cursor, and yields StreamPage objects with the new cursor map. If the default contacts delta endpoint is missing or unsupported, it quietly moves on for that one case.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It gets extra folders from _list_contact_folders, reads changes through _graph_delta_pages, and uses the cursor map helpers to preserve progress across folders.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed calendar events from a bounded calendar window. It looks one year back and two years ahead so the sync covers relevant calendar activity without asking for an unlimited range.

**Data flow**: It receives an HTTP client and optional cursor. It builds start and end times around the current moment, then reads the calendarView delta feed and yields each StreamPage it receives.

**Call relations**: OutlookConnector.paginate calls this for the events stream. This function prepares the calendar-specific date range, then hands the actual delta-feed reading to _graph_delta_pages.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s Outlook mail folders. Message syncing needs these IDs because it reads message deltas one folder at a time.

**Data flow**: It receives an HTTP client, pages through the mail folder listing, extracts each valid folder id, and returns a list of folder ID strings.

**Call relations**: OutlookConnector._message_delta_pages calls this before reading messages. The returned folder list becomes the set of places where message delta feeds are opened.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s additional Outlook contact folders. Contact syncing uses this so it does not only read the default contacts area.

**Data flow**: It receives an HTTP client, pages through the contact folder listing, extracts each valid folder id, and returns a list of folder ID strings.

**Call relations**: OutlookConnector._contact_delta_pages calls this before reading folder-based contacts. The returned IDs are used to open each folder’s contacts delta feed.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into friendlier records with common field names the rest of the system expects. It keeps the original data but adds convenient fields such as email, snippet, sender, or event start time.

**Data flow**: It receives one raw record and the stream it came from. For contacts, it adds names, first email, phone, and created_at; for messages, it adds subject, snippet, sender, sent time, and thread IDs; for events, it adds title, plain description, time range, and location. For other streams it returns the record unchanged.

**Call relations**: The connector framework calls this after records are read. It uses _first_email, _phone, _strip_html, and get_path to pull useful values out of Microsoft Graph’s nested record shapes.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Turns a saved JSON cursor map back into a Python dictionary. This lets the connector remember a separate Microsoft Graph bookmark for each mail or contact folder.

**Data flow**: It receives a raw cursor string or null. If the string is missing, invalid JSON, or not a dictionary, it returns an empty map; otherwise it returns a dictionary of folder IDs to cursor links, keeping only non-empty string values.

**Call relations**: The message and contact delta readers call this at the start of their work. It translates the runner’s stored cursor into the per-folder bookmarks those readers need.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns the per-folder cursor dictionary into a JSON string that can be saved as the stream cursor. This is how the next sync run knows where each folder stopped.

**Data flow**: It receives a dictionary of folder IDs to cursor links. If the dictionary has entries, it serializes it as sorted JSON; if it is empty, it returns null.

**Call relations**: The message and contact delta readers call this after updating folder cursors. The encoded result is placed on each yielded StreamPage so the sync runner can store the newest bookmark.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).

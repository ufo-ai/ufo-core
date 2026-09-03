# Workspace, communications, and office-suite sources  `stage-15.1.1`

This stage is part of the system’s behind-the-scenes intake work. It connects to workplace tools, checks what has changed, and turns emails, meetings, files, chats, and calendars into steady “source streams,” meaning ordered records the rest of the product can store, search, and update.

The Google pieces cover the main Google Workspace apps. Gmail reads mailbox changes and avoids rereading everything each time. Google Calendar reads events and also records attendees separately. Google Docs, Drive, Meet, and Sheets fetch documents, files, meeting notes or transcripts, and spreadsheet rows, then convert them into searchable text or structured records. The shared Google helper decides whether an API error means “you do not have access” or “try again later because Google is limiting requests.”

The Microsoft pieces do the same kind of translation for Outlook and Teams through Microsoft Graph, Microsoft’s web doorway into mail, calendars, contacts, teams, chats, channels, and messages. Slack reads users, channels, messages, threads, and participants. Together, these connectors act like adapters for different office tools, making them all look alike to the rest of the system.

## Files in this stage

### Google mail and API handling
Gmail ingestion and shared Google API error classification establish mailbox syncing and common permission/quota behavior for Google connectors.

### `extensions/sources/ufo_ext_sources/providers/gmail.py`

`io_transport` · `source sync runs`

Gmail does not hand over an email as one simple block of text. A message is stored as a nested MIME tree, which means its subject, sender, recipients, plain text, and HTML body may all be in different places, and the body text is encoded. This file is the translator between Gmail’s API and the project’s source-sync system.

On a first run, the connector lists message IDs inside a chosen backfill window, such as the last 30 days. It then fetches each message body and flattens Gmail’s nested shape into a simple record: ID, thread ID, subject, sender, recipients, labels, text body, HTML body, and direction. It also records Gmail’s history ID, which works like a bookmark in Gmail’s change log.

On later runs, it asks Gmail for changes since that history ID. Added messages are fetched. Deleted messages are reported as tombstones, meaning “remove this item from the synced copy.” If Gmail says the old bookmark has expired, the connector asks the wider system to start over safely. If the user’s Google grant lacks Gmail read permission, the stream is skipped rather than treated as a broken run.

The file also overrides rendering. Instead of showing raw Gmail JSON, it produces something close to what a person reads: From, To, Cc, Subject, then the message body, stripping HTML when needed.

#### Function details

##### `GmailConnector.paginate_source`  (lines 94–104)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector entry point used by the source-sync runner to ask Gmail for pages of records. It accepts the runner’s cursor and backfill window, then forwards the real work to the Gmail-specific pagination method.

**Data flow**: It receives an HTTP client, a stream description, the last saved cursor if there is one, and an optional backfill cutoff date. It ignores the self-user ID because Gmail’s API path already uses the authenticated user. It returns an asynchronous stream of pages produced by `GmailConnector.paginate`.

**Call relations**: The wider sync framework calls this method when it wants Gmail records. This method is a thin doorway: it immediately calls `GmailConnector.paginate`, which decides whether to do an initial backfill or a change-only sync.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 106–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Gmail messages. It decides whether to list an initial window of messages or walk Gmail’s change history, then yields pages containing new records, deleted IDs, and the next cursor.

**Data flow**: It receives an HTTP client, the stream being synced, an optional Gmail history cursor, and an optional backfill cutoff. If there is no cursor, it calls `_backfill` to collect message IDs and seed a cursor. If there is a cursor, it calls `_history` to find additions and deletions. It fetches full message bodies in chunks, wraps the results in `StreamPage` objects, and yields them to the sync system. If Gmail refuses access because the grant lacks the read scope, it raises `StreamSkipped` so the run is recorded as skipped rather than failed.

**Call relations**: `GmailConnector.paginate_source` hands control to this method. This method then coordinates `_backfill`, `_history`, and `_fetch_bodies`. It also asks `google.refused_for_scope` to distinguish a missing-permission problem from other HTTP failures.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 2 external calls (__init__, refused_for_scope).


##### `GmailConnector._backfill`  (lines 145–170)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time scan of a mailbox, limited by the configured backfill window when one is provided. It gathers message IDs and chooses the starting Gmail history ID for future incremental syncs.

**Data flow**: It receives an HTTP client and an optional cutoff date. First it reads the mailbox profile’s current history ID as a safe floor. Then it repeatedly calls Gmail’s message-list endpoint, using `after:<epoch seconds>` when a cutoff exists, and collects message IDs from each page. When listing is complete, it calls `_seed_history_id` to decide the cursor to save. It returns the collected message IDs and the next history cursor.

**Call relations**: `GmailConnector.paginate` calls this when there is no saved cursor. It relies on `_profile_history_id` before listing and `_seed_history_id` after listing so the connector can leave backfill mode even when the window contains no messages.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 172–175)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail’s mailbox profile to find the current history ID. That ID acts like a bookmark in Gmail’s change log.

**Data flow**: It receives an HTTP client, asks Gmail for the authenticated user’s profile, reads the `historyId` field if it is a string, and returns that value. If the profile does not contain a usable history ID, it returns `None`.

**Call relations**: `GmailConnector._backfill` calls this before listing messages. Reading it before the listing matters because it gives an empty backfill window a safe cursor without skipping mail that might arrive during the scan.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 177–201)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the history ID that future runs should start from after an initial backfill. It prefers the newest listed message’s history ID, but falls back to the profile history ID when there were no messages or the newest message vanished.

**Data flow**: It receives an HTTP client, the list of message IDs found during backfill, and a previously read floor history ID. If there are message IDs, it fetches the first one in minimal form and returns its `historyId` when available. If that message has disappeared, or if there were no messages, it returns the floor value instead.

**Call relations**: `GmailConnector._backfill` calls this after collecting IDs. Its result becomes the cursor that `GmailConnector.paginate` will pass back to the sync framework, allowing the next run to use `_history` instead of repeating the backfill forever.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 203–238)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This walks Gmail’s change log from a saved history ID. It finds which messages were added and which were deleted since the last successful sync.

**Data flow**: It receives an HTTP client and a starting history ID. It pages through Gmail’s history endpoint, extracts message IDs from added and deleted entries, updates the latest seen history ID, and returns three things: IDs that are currently net-added, IDs that were deleted, and the next cursor. If Gmail reports that the old history ID is too old, it raises `CursorExpired` so the larger system can refetch safely.

**Call relations**: `GmailConnector.paginate` calls this during normal incremental syncs. Inside the loop, it calls `_message_ids` to pull message IDs out of Gmail’s nested history records.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 240–254)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This fetches the full content for a batch of Gmail message IDs. It turns each raw Gmail message into the simpler record shape used by the sync system.

**Data flow**: It receives an HTTP client and a list of message IDs. For each ID, it asks Gmail for the full message. If a message disappeared between the history listing and the body fetch, a 404 is ignored. Other HTTP errors are raised. Each successful raw message is passed through `_flatten_message`, and the resulting records are returned as a list.

**Call relations**: `GmailConnector.paginate` calls this after it has collected IDs from either `_backfill` or `_history`. `_fetch_bodies` then delegates the Gmail-shape-to-record-shape conversion to `_flatten_message`.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 256–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Gmail record into readable prose. It makes an email look like an email, rather than a raw data object.

**Data flow**: It receives a flattened record and the stream description. For the Gmail messages stream, it reads the subject, sender, recipients, and body fields. It formats a title, builds header lines such as From and Subject, chooses the best available body text, and returns both the title and the rendered text. For other streams, it falls back to the parent connector’s renderer.

**Call relations**: The sync or recall layer calls this when it needs text to store or show. It calls `_str` for safe subject reading, `_format_contact` and `_format_recipients` for human-friendly addresses, and `_message_body` to choose plain text, HTML-derived text, or snippet content.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 279–288)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts message IDs from Gmail history entries. Gmail wraps each ID inside a small nested object, so this helper pulls out just the usable strings.

**Data flow**: It receives any value that is expected to be a list of history entries. It skips malformed entries, looks for `entry.message.id`, keeps non-empty string IDs, and returns them as a list.

**Call relations**: `GmailConnector._history` calls this for both added-message and deleted-message sections. That lets the history walker work with simple sets of IDs instead of Gmail’s nested response shape.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 291–318)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Gmail message into the flat record the rest of the system expects. It pulls useful headers and decoded bodies out of Gmail’s nested payload.

**Data flow**: It receives the raw dictionary returned by Gmail’s full message endpoint. It reads selected headers, extracts plain-text and HTML bodies, parses the sender and recipients, collects labels, and builds a new dictionary with fields such as `id`, `subject`, `from_handle`, `to`, `body_text`, and `direction`. The result is easier to render, index, and compare.

**Call relations**: `GmailConnector._fetch_bodies` calls this for every successfully fetched message. It uses `_extract_bodies` for MIME body text, `_parse_first_address` for the sender, and `_addresses` for recipient lists.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 321–335)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail MIME payload for the first plain-text body and the first HTML body. MIME is the email packaging format that can nest many parts, such as text, HTML, and attachments.

**Data flow**: It receives the message payload dictionary. It walks through the payload tree, decodes body data for `text/plain` and `text/html` parts, and remembers the first body found for each type. It returns a pair: plain text or `None`, then HTML text or `None`.

**Call relations**: `_flatten_message` calls this while building a simple record. The actual tree traversal happens in the nested `_extract_bodies.walk` helper.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 325–332)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive tree-walker inside `_extract_bodies`. It checks one MIME part, then checks that part’s children.

**Data flow**: It receives one part of Gmail’s payload tree. If the part is plain text or HTML and contains encoded data, it decodes the data with `_b64url_decode` and stores it if that body type has not already been found. Then it repeats the same process for each child part.

**Call relations**: Only `_extract_bodies` uses this nested helper. Its job is like opening envelopes inside envelopes until the readable message body is found.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 338–344)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body text format into normal text. Gmail stores message part bodies as URL-safe base64, which is an encoded text representation commonly used for safe transport.

**Data flow**: It receives an encoded string. It adds missing padding characters if needed, decodes the URL-safe base64 bytes, converts them to UTF-8 text while replacing invalid characters, and returns the decoded string. If the encoded data is invalid, it returns an empty string.

**Call relations**: `_extract_bodies.walk` calls this whenever it finds a plain-text or HTML body part. It uses Python’s `base64.urlsafe_b64decode` to do the actual decoding.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 347–354)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This reads the first email address from a header such as `From`. It separates the mailbox address from the display name.

**Data flow**: It receives a header string or `None`. If there is no header, it returns two `None` values. Otherwise it asks Python’s email parser to split the header into name-and-address pairs, takes the first pair, lowercases the address, and returns the address plus the display name when present.

**Call relations**: `_flatten_message` calls this to turn the raw sender header into `from_handle` and `from_display_name`. It relies on `email.utils.getaddresses`, which understands common email address formats.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 357–364)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This reads all usable email addresses from a recipient header such as `To` or `Cc`. It returns them in a consistent record shape.

**Data flow**: It receives a header string or `None`. With no header, it returns an empty list. Otherwise it parses all name-and-address pairs, skips entries without an address, lowercases each address, and returns dictionaries containing `handle` and `display_name`.

**Call relations**: `_flatten_message` calls this for the `to` and `cc` headers. It uses Python’s email address parser so the rest of the code does not need to understand the many ways email headers can be written.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 367–372)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable label. If a display name exists, it produces text like `Jane Doe <jane@example.com>`; otherwise it uses just the email address.

**Data flow**: It receives a possible email handle and a possible display name. If the handle is missing or not a string, it returns an empty string. If the display name is a non-empty string, it combines name and address; otherwise it returns the address alone.

**Call relations**: `GmailConnector.render` uses this for the sender, and `_format_recipients` uses it for each recipient. It keeps address formatting consistent across rendered email headers.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 375–382)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient records into one comma-separated line for display. It is used for headers like To and Cc.

**Data flow**: It receives any value that should be a list. If it is not a list, it returns an empty string. For each dictionary item in the list, it calls `_format_contact` and joins the results with commas.

**Call relations**: `GmailConnector.render` calls this when building the readable email header block. It delegates each single-person formatting decision to `_format_contact`.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 385–393)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for an email. It prefers plain text, falls back to HTML converted into text, and finally uses Gmail’s short snippet if no full body is available.

**Data flow**: It receives a flattened message record. It first checks `body_text` and returns trimmed text if present. If not, it checks `body_html` and runs it through `_HtmlText.extract` to strip tags and keep readable text. If neither body exists, it returns the trimmed snippet or an empty string.

**Call relations**: `GmailConnector.render` calls this after building the email headers. This helper ensures the rendered message has the clearest available body without exposing raw HTML.

*Call graph*: called by 1 (render).


##### `_str`  (lines 396–397)

```
def _str(value: Any) -> str
```

**Purpose**: This safely treats a value as a string only when it really is one. It avoids accidentally rendering non-string values as subjects or other text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: `GmailConnector.render` calls this when reading the subject field. That keeps title-building simple and avoids type surprises from incoming records.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 404–406)

```
def __init__(self) -> None
```

**Purpose**: This prepares a small HTML-to-text parser for one email body. It creates a place to collect readable text pieces as the parser scans the HTML.

**Data flow**: It receives no outside data beyond the new parser instance. It initializes Python’s `HTMLParser` with automatic character reference conversion, then creates an empty list for collected text fragments. The parser object is ready to receive HTML.

**Call relations**: `_HtmlText.extract` creates an instance of this parser whenever an email has only HTML body text. The parser’s later callbacks fill the `_parts` list.


##### `_HtmlText.extract`  (lines 409–414)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It removes tags and attributes while preserving sensible line breaks around block-like elements.

**Data flow**: It receives raw HTML text. It creates a parser, feeds the HTML into it, joins the collected text fragments, normalizes extra whitespace on each line, drops empty lines, and returns the cleaned text.

**Call relations**: `_message_body` calls this when no plain-text email body is available. During parsing, Python’s HTML parser calls `_HtmlText.handle_data`, `_HtmlText.handle_starttag`, and `_HtmlText.handle_endtag` as it sees content and tags.


##### `_HtmlText.handle_data`  (lines 416–417)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records actual words found inside an HTML email. It ignores markup and keeps only the human-readable character data.

**Data flow**: It receives a text chunk from the HTML parser. It appends that chunk to the parser’s internal list. Nothing is returned; the parser’s collected parts are changed.

**Call relations**: Python’s HTML parser calls this while `_HtmlText.extract` is feeding it HTML. The collected text later becomes part of the cleaned plain-text body.


##### `_HtmlText.handle_starttag`  (lines 419–421)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when the parser enters an HTML tag that usually starts a new visual block, such as a paragraph or table row. This keeps converted email text from running together.

**Data flow**: It receives a tag name and that tag’s attributes. If the tag is in the known block-tag set, it appends a newline marker to the collected parts. It ignores attributes and returns nothing.

**Call relations**: Python’s HTML parser calls this during `_HtmlText.extract`. It works with `_HtmlText.handle_endtag` so HTML structure leaves readable spacing even after tags are removed.


##### `_HtmlText.handle_endtag`  (lines 423–425)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when the parser exits an HTML tag that usually ends a visual block. It helps preserve paragraph-like spacing in the stripped text.

**Data flow**: It receives a tag name. If the tag is in the known block-tag set, it appends a newline marker to the collected parts. It returns nothing and changes only the parser’s collected text fragments.

**Call relations**: Python’s HTML parser calls this during `_HtmlText.extract`. Together with `_HtmlText.handle_starttag`, it makes the final plain text easier to read after HTML tags are removed.


### `extensions/sources/ufo_ext_sources/providers/google.py`

`domain_logic` · `request handling`

Google APIs often return the same broad HTTP errors, especially 401 and 403, for very different problems. A 401 or 403 can mean “this account was not granted permission to read this data,” which will not fix itself. It can also mean “you have hit a usage limit,” which may clear once Google’s quota window resets. This file exists so the connector does not treat those two cases as the same.

The key idea is simple: first look at the HTTP status code, then inspect Google’s error body for quota-specific clues. If the response body says the status is RESOURCE_EXHAUSTED, or if one of Google’s detailed error reasons says things like quotaExceeded or rateLimitExceeded, this file treats it as a quota problem. A quota problem should be retried later, like waiting for a busy ticket counter to reopen.

If the status is 401 or 403 and it is not a quota problem, the file treats it as a permission or scope refusal. In that case, retrying will not help because the account grant itself is missing something. The caller can then skip that stream instead of repeatedly failing the run. Without this distinction, the system could either give up on streams that would work later, or keep retrying streams that can never work until the user reconnects with the right permissions.

#### Function details

##### `error_detail`  (lines 31–38)

```
def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This function pulls the useful Google error details out of an HTTP error response. If the response has no readable JSON body, or does not contain the expected error object, it safely returns an empty dictionary instead of crashing.

**Data flow**: It receives an httpx.HTTPStatusError, which includes the failed HTTP response from Google. It tries to read the response body as JSON, then looks for the nested "error" object and makes sure it is really a dictionary. The result is either that error-detail dictionary or an empty dictionary when there is nothing reliable to read.

**Call relations**: refused_for_scope calls this when it needs more than just the HTTP status code. error_detail uses dict_or_empty from ufo.sdk.sources as a safety check, so malformed or unexpected response shapes do not confuse the later quota-versus-permission decision.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (dict_or_empty).


##### `is_quota_refusal`  (lines 41–45)

```
def is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This function decides whether Google refused the request because of a usage limit rather than because of missing account permission. It looks for Google’s quota markers in the parsed error details.

**Data flow**: It receives a dictionary of Google error details. It first checks whether the top-level status says RESOURCE_EXHAUSTED. If not, it looks through the detailed error entries and checks whether any reason matches known quota or rate-limit reasons. It returns True for a quota problem and False otherwise.

**Call relations**: refused_for_scope calls this after error_detail has extracted the response’s error information. is_quota_refusal uses list_or_empty from ufo.sdk.sources so that missing or oddly shaped error lists are treated as an empty list, keeping the decision safe and predictable.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (list_or_empty).


##### `refused_for_scope`  (lines 48–53)

```
def refused_for_scope(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This function answers the main question for callers: is this Google HTTP error a settled permission problem that should make the stream get skipped? It returns False for quota-related refusals, because those should be raised and retried later.

**Data flow**: It receives an httpx.HTTPStatusError from a failed Google API request. It checks whether the HTTP status code is one of the refusal statuses, 401 or 403. If so, it extracts the Google error detail with error_detail and asks is_quota_refusal whether the refusal is really about quota. It returns True only when the status is 401 or 403 and the details do not point to quota.

**Call relations**: This is the public decision point in the file. Connector code can call it when Google rejects a request, then use the result to choose between skipping a stream for missing permission or letting the error propagate into the retry and backoff path for temporary quota exhaustion.

*Call graph*: calls 2 internal fn (error_detail, is_quota_refusal).


### Google workspace content
Google Calendar, Docs, Drive, Meet, and Sheets connectors turn workspace records and office artifacts into searchable synchronized streams.

### `extensions/sources/ufo_ext_sources/providers/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the project’s source-sync system. Without it, calendar events would not be imported, updated, deleted, or turned into readable text for later recall.

The main job is to call Google’s events API for the user’s primary calendar. On the first run, it looks back 90 days and asks Google for events, including deleted ones. Google then gives back a sync token, which is like a bookmark saying “next time, continue from here.” On later runs, the connector sends that token so Google only returns changes since the last sync. If Google says the bookmark is too old, the connector signals that the cursor expired so the larger system can start fresh.

The file exposes two views of the same calendar data. The calendar_events stream stores one record per event, including title, time, location, organizer, description, and a folded-in attendee list. The event_attendees stream breaks each event into one record per invitee, useful when the system needs attendee relationships directly.

It is careful about permission problems. If the user’s Google grant does not include Calendar access, the stream is marked as skipped rather than failed. Finally, the render method turns an event record into a simple human-readable note, like a calendar card with title, time, place, attendees, and description.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 51–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches Google Calendar events page by page and turns them into stream pages the sync engine can write. It supports both full first-time imports and later incremental updates using Google’s sync token bookmark.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. If there is no cursor, it asks Google for events from the last 90 days; if there is a cursor, it asks only for changes since that bookmark. Each Google event is checked: cancelled calendar events become delete markers, normal calendar events are flattened into event records, and attendee streams are expanded into one row per invitee. It yields StreamPage objects containing new or changed records, deleted record IDs, and, on the final page, the next cursor to save.

**Call relations**: The sync engine calls this when it wants records from either the calendar_events or event_attendees stream. During that flow it hands raw event data to _flatten_event or _flatten_attendees depending on the stream. If Google says the sync token expired, it raises CursorExpired so the broader system can refetch from scratch; if Google refuses access because the grant lacks Calendar scope, it raises StreamSkipped so the run records a skip instead of treating it as a broken connector.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 5 external calls (__init__, __init__, now, timedelta, refused_for_scope).


##### `GoogleCalendarConnector.render`  (lines 111–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a stored calendar event record into readable text for recall or display. It makes calendar data feel like a simple agenda note instead of raw API fields.

**Data flow**: It receives one record and the stream it belongs to. For calendar_events, it reads the title, start and end time, location, attendees, and description, then builds a plain text body with those pieces. It returns a pair: the title to use as the record label and the rendered text body. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The larger source system calls this after records have been synced and need a human-readable representation. It uses _str to safely turn a missing or non-text title into an empty string, then assembles the display text itself.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 139–166)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the project’s standard event record shape. This removes Google-specific nesting and keeps the pieces the system cares about, such as time, organizer, title, and attendees.

**Data flow**: It receives a raw event dictionary from Google. It reads fields like id, created time, updated time, summary, description, location, start and end times, organizer, recurrence ID, and attendees. It normalizes times through _parse_when and attendee summaries through _attendee, then returns one flat dictionary ready to be written as a calendar_events record.

**Call relations**: GoogleCalendarConnector.paginate calls this whenever it sees a non-cancelled event in the calendar_events stream. Inside the conversion, it delegates small cleanup jobs to _parse_when for event times and _attendee for each invitee summary.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 169–175)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Builds the compact attendee summary stored inside a calendar event record. It keeps the invitee’s email handle, display name, and response in a consistent form.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee email to make it a stable handle, copies the display name if present, and translates Google’s response status into this system’s response wording. It returns a small dictionary that can be embedded in an event record.

**Call relations**: _flatten_event calls this while folding the event’s attendee list into the main calendar event record. It is the small helper that keeps attendee summaries consistent across synced events.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 178–204)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one Google Calendar event into separate attendee records, one per invitee. This is useful when the system needs to search, link, or reason about attendees independently from the event body.

**Data flow**: It receives a raw event dictionary from Google. It finds the event ID and organizer, then walks through the attendee list. For each attendee with an email address, it creates a record whose ID combines the event ID and attendee handle, adds event timestamps, role, response, display name, and whether the attendee is the calendar owner. The output is a list of attendee row dictionaries.

**Call relations**: GoogleCalendarConnector.paginate calls this for the event_attendees stream whenever an event is not cancelled. It calls _attendee_role for each invitee so the row can say whether that person is the organizer, a room or other resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 207–214)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in an event. It translates Google’s attendee flags into simple labels the rest of the system can understand.

**Data flow**: It receives one attendee dictionary and a separate yes-or-no value saying whether that attendee is the organizer. It checks, in order, whether the attendee is the organizer, a resource such as a room, optional, or none of those. It returns one role string: organizer, resource, optional, or required.

**Call relations**: _flatten_attendees calls this while building each attendee row. Its result becomes the role field in the per-attendee stream.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 217–226)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar time fields into one timestamp-like string format. Google represents timed events and all-day events differently, and this helper hides that difference.

**Data flow**: It receives a value that may be a Google time object. If the object contains dateTime, it returns that value as text. If it contains an all-day date, it turns the date into midnight UTC text. If the value is missing or not shaped like a time object, it returns nothing.

**Call relations**: _flatten_event calls this for both the start and end fields of an event. This lets synced event records use starts_at and ends_at without the rest of the system needing to know Google’s two different time formats.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 229–230)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents rendering code from accidentally treating missing or non-text values as display strings.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: GoogleCalendarConnector.render calls this when reading the event title. That keeps the rendered calendar note stable even if the title is absent or not a string.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Google Docs. Its job is read-only: it does not create or edit documents. First, it asks Google Drive for files whose type is “Google Doc,” skipping trashed files and, when possible, only asking for documents changed after the last saved sync time. That keeps later syncs from rereading everything.

For each Drive file it finds, it uses the document id to fetch the full document from the Google Docs API. If a file is visible in Drive but cannot be opened through Docs, for example because permission was removed or the file disappeared, the connector returns a small placeholder instead of failing the whole sync. But if Google refuses the Drive listing itself because the account lacks the needed permission scope, the stream is skipped with a clear reason.

The connector gathers documents into batches, adds useful metadata like title, URL, creation time, update time, and original Drive file details, then yields those batches to the sync engine. Finally, its render step walks the nested Google Docs body structure and pulls out paragraph text. In everyday terms, this file is like a librarian who first checks the catalog, then opens each book it can, and copies out readable text for the archive.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 51–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for the Google Docs stream. It lists Google Doc files, fetches each document, packages the document and its Drive metadata into records, and yields those records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It asks `_iter_doc_files` for Drive file pages, uses each file id to call `_document`, combines the fetched document with fields such as title, URL, creation time, update time, and Drive metadata, and emits lists of records once a batch is full or the run ends. If Google refuses access because the grant lacks the right scope or sharing permission, it turns that into a `StreamSkipped` result instead of crashing the entire source sync.

**Call relations**: During a sync, the source framework calls this method to produce Google Docs records. It relies on `_iter_doc_files` to discover candidate files and `_document` to fetch each one. If an access error happens, it asks `ufo_ext_sources.providers.google.refused_for_scope` whether the error means the stream should be skipped, then raises `StreamSkipped` with a human-readable explanation.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files); 1 external calls (refused_for_scope).


##### `GoogleDocsConnector._document`  (lines 89–98)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one full Google Doc by its file id. It also protects the sync from failing just because one listed document cannot be opened.

**Data flow**: It receives an HTTP client and a Google file id. It calls the Google Docs API for that document and returns the document data when successful. If Google replies with 403 forbidden or 404 not found, it returns a minimal record containing only the document id; any other HTTP error is passed upward.

**Call relations**: `GoogleDocsConnector.paginate` calls this for each file found through Drive. Its result is folded into the final synced record, so even unreadable or vanished documents can still appear as harmless stubs instead of stopping the whole batch.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 100–127)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists Google Docs files from Google Drive, page by page. It applies the saved cursor so the connector can sync only files modified after the previous run.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for non-trashed Google Docs, adds a `modifiedTime` filter when there is a cursor, and repeatedly calls the Drive files endpoint. Each response is cleaned into a list with `list_or_empty`, yielded if it contains files, and followed through `nextPageToken` until Google says there are no more pages.

**Call relations**: `GoogleDocsConnector.paginate` calls this first, because Drive is the catalog that tells the connector which document ids exist and changed. This function hands file batches back to `paginate`, which then fetches the actual document bodies.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 129–134)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a simple title and readable text block. The rendered text is what downstream search or recall features can show to a person.

**Data flow**: It receives one document record and the stream description. It extracts a safe title, calls `_plain_text` to pull paragraph text from the Google Docs body, builds a heading that names the provider and stream, and returns both the title and the final text.

**Call relations**: After records have been fetched, the source framework can call this method when it needs prose rather than raw Google API data. It delegates the document-body parsing to `_plain_text`, then wraps that text with a clear heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 137–153)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts the visible paragraph text from the nested structure used by the Google Docs API. It ignores document parts that do not contain paragraph text runs.

**Data flow**: It receives a document record. It looks for `body.content`, walks each paragraph, then collects the text from each paragraph element’s `textRun.content`. It joins those pieces in order, trims extra whitespace at the ends, and returns one plain string.

**Call relations**: `GoogleDocsConnector.render` calls this when it needs human-readable document text. This helper is deliberately narrow: it only converts the Google Docs body tree into prose and leaves headings, metadata, and output formatting to `render`.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledrive.py`

`io_transport` · `source sync`

Google Drive is not a single simple list. It has normal files, shared drives, and smaller collections attached to each file, such as comments and permissions. This file is the adapter that knows how to ask Google’s API for each of those pieces and present them in the shape the rest of the project expects.

The main stream is `files`. On the first run, the connector reads all live, non-trashed files and then asks Google for a special “start page token.” That token is like a bookmark in a diary: next time, the connector can ask only for changes after that point instead of rereading everything. If Google says the bookmark is too old, the connector raises `CursorExpired`, telling the wider system to start fresh.

Other streams work differently. Shared drives are reread from scratch each time. Permissions, comments, and revisions are fetched by first listing every file, then asking Google for that file’s child records. If a specific file refuses access, the connector skips that child lookup rather than failing the whole run.

The file also includes a small `render` method that turns a Drive file record into readable text with its name, MIME type, owners, and link. The connector only reads data; it does not create or edit Google Drive content.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 87–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Google Drive streams. Given a requested stream, it chooses the right Google Drive reading strategy and yields pages of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. If the stream is files, it either reads changes from the saved cursor or performs the first full file scan and saves a new change token. If the stream is shared drives, it reads shared-drive pages. If the stream is permissions, comments, or revisions, it walks through files and reads those child records. It outputs lists of records or `StreamPage` objects that may include records, deletions, and the next cursor. If Google rejects the whole stream because the account lacks Drive permission, it changes that failure into a clean skipped-stream result.

**Call relations**: The sync framework calls this when it wants data for one Google Drive stream. This method then hands the work to the more specific helpers: file listing, change listing, shared-drive listing, child-record listing, and token lookup. It also asks the shared Google helper whether an HTTP error means missing permission, so the larger run can record a skip instead of treating it as a broken connector.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 2 external calls (__init__, refused_for_scope).


##### `GoogleDriveConnector._paginate_files`  (lines 121–146)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Google Drive files in ordinary file-list pages. It is used for the first full file scan and also as the starting point for finding per-file child records.

**Data flow**: It receives an HTTP client and an optional modified-time cursor. It builds a Google Drive query for non-trashed files, optionally only files modified after the cursor, then repeatedly asks Google for one page at a time. Each response’s `files` value is normalized into an empty-or-list form, and non-empty pages are yielded. Pagination continues until Google stops returning a next-page token.

**Call relations**: The main `paginate` method uses this for the initial `files` stream read. `_paginate_file_children` also uses it to discover which files exist before asking for each file’s permissions, comments, or revisions.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 148–153)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current change-tracking bookmark. The connector stores this token so later syncs can fetch only what changed.

**Data flow**: It receives an HTTP client and calls Google’s start-page-token endpoint. From the response, it reads `startPageToken`. If that value is a real non-empty string, it returns it; otherwise it returns nothing.

**Call relations**: After `paginate` finishes the first full file listing, it calls this helper to mark the point from which future file changes should be read. That token is then yielded inside a `StreamPage` so the wider sync system can save it.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 155–200)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads the Google Drive change feed for files after a previously saved token. It lets later syncs update, delete, or add files without scanning the whole Drive again.

**Data flow**: It receives an HTTP client and a saved change cursor. It asks Google for change pages, turns changed file objects into records, and turns removed or trashed files into delete markers. For each page, it yields a `StreamPage` containing the new or updated records, deleted file IDs, and the next cursor. If Google says the cursor has expired, it raises `CursorExpired` so the caller knows a full refresh is needed.

**Call relations**: The main `paginate` method calls this when the `files` stream already has a cursor. This helper packages Google’s change-feed response into the project’s stream-page format, including deletions, so the storage layer can keep its copy of Drive in sync.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 202–219)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the list of shared drives visible to the account. Shared drives are read as a full list each run rather than through a change cursor.

**Data flow**: It receives an HTTP client, repeatedly calls Google’s shared-drive endpoint, converts the returned `drives` field into a safe list, and yields any non-empty pages. It follows Google’s next-page token until there are no more pages.

**Call relations**: The main `paginate` method calls this when the requested stream is `shared_drives`. It supplies the shared-drive records directly to the sync engine without involving the file-change cursor flow.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 221–259)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads records that belong to individual files, such as permissions, comments, or revisions. It works by visiting each file first, then asking Google for that file’s child collection.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. First it lists all files. For each file with a valid ID, it calls the matching child endpoint, follows child-page tokens, and reads the child records. If a cursor and cursor field are available, it filters out older child records. Before yielding records, it adds the parent file’s ID and name so each child item can be traced back to its file. If Google refuses access to one file’s child data with a common not-allowed or not-found response, it skips that file’s child lookup and continues.

**Call relations**: The main `paginate` method calls this for permissions, comments, and revisions. This helper depends on `_paginate_files` to know which files to visit, then produces enriched child records for the rest of the sync pipeline.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 261–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a small human-readable text body. That text can be used for recall, search, or display without needing to inspect the raw API response.

**Data flow**: It receives one record and its stream description. For non-file streams, it lets the base connector render the record. For file records, it pulls out the name, MIME type, owners, and web link, builds a title and a few readable lines, and returns both. It uses `_str` to safely treat a missing or non-text name as an empty string.

**Call relations**: This is used when the broader source system wants a readable version of a synced record. It only customizes the `files` stream; everything else is handed back to the parent `RestConnector` rendering behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 279–280)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into text only if it is already a string. It avoids accidentally rendering non-text values as misleading titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. If not, it returns an empty string.

**Call relations**: The `render` method calls this when reading a file name from a Google Drive record. It acts as a small safety check before building the human-readable title.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googlemeet.py`

`io_transport` · `during source sync`

Google Meet does not expose “a meeting summary” as one simple object. Instead, the useful material is spread across conference records, transcript sessions, transcript entries, and sometimes a linked Google Docs file for smart notes. This file is the adapter that gathers those pieces and reshapes them into one plain-text page per meeting.

The connector starts by listing Google Meet conference records from newest to oldest. For each conference, it asks Google Meet for transcript artifacts and smart-note artifacts. If a conference has neither, it is skipped as contentless, but its start time can still move the sync cursor forward so the system does not keep rereading the same empty window forever. The connector also looks back one day when resuming from a cursor, because transcripts and smart notes can appear after the meeting has ended.

For transcripts, it fetches the per-speaker entries and turns them into dialogue. For smart notes, it follows the Google Docs destination when possible and extracts the document’s plain text. If Google Docs says the file is missing or not readable, the run does not fail; the page keeps the document link instead. If Google Meet refuses access because the connected account lacks permission, the stream is marked as skipped rather than treated as a broken system.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 55–89)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main reader for the Google Meet stream. It lists meeting conference records page by page, fetches their transcripts and smart notes, and yields batches of finished meeting records to the wider sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from the previous sync. It builds Google Meet API query parameters, including a one-day lookback if there is a cursor, reads conference pages, expands each conference into a full record, keeps only conferences with artifacts, and outputs StreamPage objects with records plus the next cursor. If Google refuses because of missing permission, it changes that failure into a skipped stream.

**Call relations**: The sync framework calls this when it wants Google Meet data. Inside the loop it uses _lookback to avoid missing late-generated artifacts, _max_start_time to advance the cursor, and _conference_record to build each meeting-shaped record. When Google access is refused, it asks google.refused_for_scope whether this is a permission problem and then raises StreamSkipped for the framework to record.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 3 external calls (__init__, list_or_empty, refused_for_scope).


##### `GoogleMeetConnector._conference_record`  (lines 91–114)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete internal record for one Google Meet conference. It gathers the conference’s transcript and smart-note artifacts and attaches basic meeting information like start time, end time, and space.

**Data flow**: It receives one raw conference object from Google. It reads the conference resource name, fetches related transcript artifacts and smart-note artifacts, converts each artifact into a richer record, and returns one dictionary representing the meeting. The result includes a stable id, title, original conference name, timing fields, transcripts, and smart notes.

**Call relations**: paginate calls this for every conference returned by the Meet API. It delegates artifact listing to _artifacts, transcript shaping to _transcript, smart-note shaping to _smart_note, and small string/id cleanup to _str and _resource_id.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 116–133)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind under a conference, such as all transcript sessions or all smart-note sessions. It hides Google’s paged API behind a simple list.

**Data flow**: It receives the parent conference name and the collection name to read. If there is no parent name, it returns an empty list. Otherwise it repeatedly calls the Google Meet endpoint, follows page tokens, collects artifact objects, and returns the full list.

**Call relations**: _conference_record calls this twice for each meeting: once for transcripts and once for smart notes. It uses list_or_empty so missing or oddly shaped response fields become an empty list instead of crashing the connector.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 135–147)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google into the connector’s transcript record. It includes the transcript’s document link and the individual spoken entries.

**Data flow**: It receives a raw transcript object. It extracts its name, id, state, timing, and Google Docs destination, then calls _transcript_entries to fetch the spoken lines. It returns a dictionary ready to be included in the meeting record.

**Call relations**: _conference_record calls this for every transcript artifact found by _artifacts. It uses _docs_destination to preserve the durable Google Docs link, _resource_id for a compact id, _str for safe string extraction, and _transcript_entries for the actual dialogue content.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 149–182)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual lines of a transcript, including who spoke, what they said, and when. It is what turns a transcript artifact from a pointer into readable meeting dialogue.

**Data flow**: It receives a transcript resource name. If the name is empty, it returns no entries. Otherwise it reads the transcript entries endpoint page by page, converts each entry into a smaller dictionary, and returns the collected list. If Google says the related document or artifact is missing or forbidden, it returns whatever entries were already collected instead of failing.

**Call relations**: _transcript calls this while building a transcript record. It uses _str and _resource_id to normalize names and ids, and list_or_empty to safely walk the Google response. Its output is later formatted by _dialogue through the render path.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 184–199)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a record, and tries to include the text of the linked Google Doc. Smart notes are the AI-generated meeting summaries produced by Google’s Gemini feature.

**Data flow**: It receives a raw smart-note object. It extracts id, name, state, timing, and Google Docs destination. If there is a document id, it asks _document_text for the document’s plain text and adds that text as the note body when available. It returns the finished smart-note dictionary.

**Call relations**: _conference_record calls this for every smart-note artifact found under a meeting. It uses _docs_destination, _resource_id, and _str for basic cleanup, and hands off to _document_text when it can try to inline the summary text.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 201–210)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a Google Docs document and extracts only its plain text. It is used so smart-note summaries can be stored directly in the meeting page instead of only as a link.

**Data flow**: It receives a Google Docs document id. It safely encodes that id for a URL, fetches the document from the Docs API, and passes the returned document structure to _plain_text. If Docs says the file is forbidden or missing, it returns an empty string; other errors are allowed to stop the run.

**Call relations**: _smart_note calls this when a smart note points to a Docs document. It uses urllib.parse.quote to make the id safe inside the URL and _plain_text to convert Google’s nested document format into ordinary text.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 212–229)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a collected meeting record into the final human-readable page text. It is the last step that turns structured API data into recallable prose.

**Data flow**: It receives one meeting record and the stream description. For the Google Meet meeting_artifacts stream, it builds a title, a labeled conference summary, a transcript section, and an AI-summary section, then returns the title and full page body. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The source framework calls this after records have been fetched. It relies on _labeled for simple metadata blocks, _transcripts_section for transcript formatting, _smart_notes_section for note formatting, and _str to avoid non-string values leaking into text output.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 232–248)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript records for a meeting into a readable “Transcripts” section. It includes transcript metadata and speaker dialogue.

**Data flow**: It receives an unknown value that should be a list of transcripts. It safely treats missing or non-list data as empty, then for each transcript builds a small labeled metadata block and a dialogue block. It returns one text section, or an empty string if there are no transcripts.

**Call relations**: render calls this while assembling the final meeting page. It uses _labeled for transcript state, time, and document link; _dialogue for the spoken content; _str for safe text fields; and list_or_empty for tolerant input handling.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 251–267)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats all smart notes for a meeting into a readable “AI summaries” section. It includes note metadata and the summary body when the linked Google Doc could be read.

**Data flow**: It receives an unknown value that should be a list of smart notes. It safely turns that into a list, then for each note builds labeled state/time/link metadata and appends the body text if present. It returns the finished section or an empty string when there are no notes.

**Call relations**: render calls this when building the meeting page. It uses _labeled for the note details, _str for safe strings, and list_or_empty so absent smart notes simply mean no section is produced.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 270–283)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns transcript entry records into a clean speaker-by-speaker conversation. It also joins back-to-back entries by the same speaker so the output reads more naturally.

**Data flow**: It receives an unknown value that should contain transcript entries. It walks each entry, ignores blank text, finds a readable speaker name, and either appends the text to the previous line for the same speaker or starts a new line. It returns a newline-separated dialogue string.

**Call relations**: _transcripts_section calls this to format transcript entries. It uses _speaker to name the participant, _str to safely extract text, and list_or_empty to cope with missing entry lists.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 286–299)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts ordinary text from the nested structure returned by the Google Docs API. It strips away document layout details and keeps the readable words.

**Data flow**: It receives a Google Docs document record. It looks inside the document body, scans paragraph elements, collects text-run content, joins those text chunks together, trims surrounding whitespace, and returns the result.

**Call relations**: _document_text calls this after fetching a Docs document. It is deliberately narrow: it only knows how to pull paragraph text from Google’s document shape and does not make network calls itself.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 302–309)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls the Google Docs destination information out of a Meet artifact. That destination is important because it is a durable link to the generated transcript or smart note.

**Data flow**: It receives a transcript or smart-note record. If the record has a docsDestination object, it extracts the document id and export URL as strings; otherwise it returns an empty dictionary. The returned fields can be merged into the artifact record.

**Call relations**: _transcript and _smart_note call this while shaping Google artifacts. It uses _str so missing or non-string document fields become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 312–318)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This calculates the newest meeting start time seen so far. That value becomes the sync cursor, which tells the next run where to resume.

**Data flow**: It receives the current page of conference records and the previous cursor. It compares each conference’s startTime string with the current best value and returns the greatest one it finds, or the original cursor if nothing newer appears.

**Call relations**: paginate calls this after reading each page of conferences. Its result is placed into StreamPage so the wider sync engine can remember progress even when some conferences have no artifacts.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 321–323)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor one day earlier. It prevents the connector from missing transcripts or smart notes that Google generates after the meeting has ended.

**Data flow**: It receives an ISO timestamp string, converts it into a datetime, subtracts the configured one-day lookback, and returns a timestamp string formatted for Google’s API filter.

**Call relations**: paginate calls this when it has a previous cursor and needs to build the Meet API filter. The result widens the next read window while _max_start_time still lets the cursor advance normally.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 326–327)

```
def _resource_id(name: str) -> str
```

**Purpose**: This takes a full Google resource name and returns only the last part. It gives the connector compact ids instead of long slash-separated API paths.

**Data flow**: It receives a string such as a Google resource name. If it is not empty, it splits at the last slash and returns the final segment; otherwise it returns an empty string.

**Call relations**: _conference_record, _transcript, _transcript_entries, _smart_note, and _speaker call this whenever they need a shorter id or display name from a Google resource path.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 330–332)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses a readable speaker label for a transcript entry. If Google provides no usable participant name, it falls back to “Participant.”

**Data flow**: It receives the participant value from a transcript entry. It safely converts that to a string, extracts the last resource segment as the speaker name, and returns that name or the fallback label.

**Call relations**: _dialogue calls this for every spoken transcript entry. It uses _str and _resource_id so the dialogue formatter can focus on building readable conversation lines.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 335–336)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only if it is already a string. It prevents unexpected numbers, objects, or missing values from being printed as misleading text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: Many functions call this while reading Google API responses, including record builders and rendering helpers. It acts like a simple filter between loosely shaped API data and the connector’s text output.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 339–340)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats a small list of label-and-value pairs into human-readable metadata lines. Empty values are left out so the final page is not cluttered.

**Data flow**: It receives pairs like label and value. For every pair with a non-empty value, it creates a line in the form “label: value” and joins the lines with newlines. It returns the finished block of text.

**Call relations**: render, _transcripts_section, and _smart_notes_section call this when building the final page. It gives all metadata blocks a consistent, easy-to-scan shape.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/providers/googlesheets.py`

`io_transport` · `source sync run`

This connector is the bridge between the project and Google Sheets. It first asks Google Drive for spreadsheet files the user can access, then asks the Sheets API for each spreadsheet’s tabs and cell values. Without this file, the system could not turn a user’s spreadsheets into recallable records.

The file is careful about incremental syncing, which means it tries to fetch only files changed since the last run. It uses Google Drive’s modified time as the bookmark, or “cursor,” for where syncing left off. Because several files can share the same timestamp, it uses an inclusive search and may re-read some files rather than risk missing any.

It also has detailed permission behavior. If the whole Google grant is missing required access, the stream is skipped. If only one spreadsheet or tab is refused, the connector records that refusal and keeps syncing the rest, like a delivery route that marks one locked door and continues down the street. Later runs retry refused files so newly granted access can fill in what was missed.

The connector exposes three streams: spreadsheets, sheet tabs, and sheet cell values. It also renders each record into readable text, so a spreadsheet becomes a title plus tab list, and a sheet grid becomes simple row text.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 153–203)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main paging loop for syncing Google Sheets records. It decides what to fetch next, groups records into pages, advances the cursor, and remembers any files that were refused so they can be retried later.

**Data flow**: It starts with an incoming cursor, decodes it into a last-seen modified time plus any carried refused file IDs, then walks through spreadsheet visits from Drive. For each file, it asks for records for the requested stream, adds them to the current page, updates the watermark, and yields a StreamPage when the page is large enough. After the normal listing, it separately retries carried refused files and finally emits a checkpoint if the cursor changed.

**Call relations**: The sync runtime calls this when it wants records for one Google Sheets stream. It relies on _decode_cursor at the start, _spreadsheet_visits for the normal Drive listing, _visit_records to turn each spreadsheet into the right kind of records, _carried_visit for retrying refused files, _settled to update the refused set, and _encode_cursor before handing pages back to the runtime.

*Call graph*: calls 7 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _settled); 2 external calls (__init__, refused_for_scope).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 205–230)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists spreadsheet files from Google Drive in modified-time order. It is the connector’s source of “which spreadsheets should we consider for this sync run?”

**Data flow**: It receives an HTTP client and an optional watermark. It builds a Drive query for untrashed Google Sheets files, adds a modified-time filter when a watermark exists, follows Drive page tokens, and yields batches of file metadata. Empty or missing file lists are normalized so the rest of the code can loop safely.

**Call relations**: _spreadsheet_visits calls this to get Drive file batches before enriching each file with Sheets metadata. It does the Drive-list part of the larger Drive-list to Sheets-read flow.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 232–240)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This function turns Drive file listings into spreadsheet visits. A visit is a small package saying which file was examined, what spreadsheet record was found, and whether access was refused.

**Data flow**: It reads batches from _iter_spreadsheet_files, checks each file for a usable ID, and asks _file_visit to fetch or build the spreadsheet record. It yields one _FileVisit for each valid spreadsheet ID.

**Call relations**: paginate calls this during the normal listing phase. It sits between raw Drive search results and the stream-specific record creation done later by _visit_records.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 242–256)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This function retries a spreadsheet that was refused in an earlier run. It checks whether the file is now readable, gone, trashed, or still refused.

**Data flow**: It receives a file ID from the carried-refusal list. It asks Drive for that file’s metadata, including whether it is trashed. If Google says the file is gone or still forbidden in a per-file way, it returns a visit with no record and the appropriate refused flag. If the file is trashed, it clears the refusal. Otherwise it hands the file to _file_visit for normal spreadsheet reading.

**Call relations**: paginate calls this after the normal Drive listing, but only for carried IDs that were not already reached in the listing. It uses _is_per_file_refusal to tell a file-specific denial from broader failures, and it hands successful cases to _file_visit.

*Call graph*: calls 2 internal fn (_file_visit, _is_per_file_refusal); called by 1 (paginate); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._file_visit`  (lines 258–285)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This function fetches the Sheets metadata for one spreadsheet and combines it with Drive metadata. If the Sheets API refuses that one file, it still creates a minimal spreadsheet record from Drive data.

**Data flow**: It receives a Drive file record and file ID. It asks the Sheets API for spreadsheet details such as title and tab list. On a per-file refusal, it marks the visit as refused and falls back to a minimal metadata shape. It returns a _FileVisit containing the combined record, timestamps, URL, title, and refusal state.

**Call relations**: _spreadsheet_visits uses this for files found in the normal listing, and _carried_visit uses it when retrying a previously refused file. It uses _is_per_file_refusal to decide whether a Sheets error can be treated as a single-file problem.

*Call graph*: calls 1 internal fn (_is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._visit_records`  (lines 287–303)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This function converts one spreadsheet visit into records for the specific stream being synced. It is the dispatcher that chooses between spreadsheet records, tab records, and cell-value records.

**Data flow**: It receives a stream description and a _FileVisit. If the visit has no record, it returns no records plus the refusal flag. For the spreadsheets stream it returns the spreadsheet record itself; for sheets it expands the spreadsheet into tab records; for sheet_values it fetches grid values and returns value records. Unknown streams fail clearly.

**Call relations**: paginate calls this for every visited or retried spreadsheet. It delegates tab expansion to _sheet_records and grid fetching to _sheet_value_records.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 305–361)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This function reads the actual cell rows for each tab in a spreadsheet. It batches tab reads for efficiency but can fall back to one-tab-at-a-time reads when a batch is refused.

**Data flow**: It receives a spreadsheet record, extracts usable tab titles and sheet IDs, and requests their values from the Sheets API in chunks. For each returned grid, it creates a value record. If a batch request is refused for a per-file reason, it retries each tab individually so only the truly refused tabs are dropped. If Google returns the wrong number of value ranges, it raises a StreamFault because records could be matched to the wrong tabs.

**Call relations**: _visit_records calls this for the sheet_values stream. It uses _quoted_sheet_range to form safe Sheets range names, _sheet_value_record to build output records, _is_per_file_refusal to classify errors, and list_or_empty to safely read Google’s response lists.

*Call graph*: calls 4 internal fn (__init__, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 3 external calls (list_or_empty, error_detail, quote).


##### `GoogleSheetsConnector.render`  (lines 363–382)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns synced Google Sheets records into readable text. That text is what downstream search or recall features can show to a person.

**Data flow**: It receives one record plus the stream it came from. For spreadsheet records it builds a title and a list of tabs; for sheet records it names the parent spreadsheet; for value records it turns rows into plain text. It returns a display title and a body of text.

**Call relations**: The broader source framework calls render when it needs human-readable content from a synced record. This function uses _str to avoid non-text titles and _grid_text to format cell rows.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 385–398)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This helper reads the saved sync cursor. It supports both old simple cursors and newer JSON checkpoints that also remember refused files.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns no watermark and no carried files. If the cursor is plain text or not the expected JSON shape, it treats it as the old watermark form. If it is a valid checkpoint, it returns the watermark, refused file IDs, and the last retried ID.

**Call relations**: paginate calls this at the start of a sync page sequence. Its output tells paginate where to begin listing and which previously refused files still need another attempt.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 401–406)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This helper writes the sync cursor that will be stored after a page. It stores just a watermark when possible, and a richer checkpoint when refused files must be remembered.

**Data flow**: It receives the current watermark, the set of refused file IDs, and the current retried ID. If there is no watermark or no refused files, it returns the simple watermark form. Otherwise it creates a JSON checkpoint with a sorted, capped refused list and the retry marker when present.

**Call relations**: paginate calls this whenever it yields a page or needs to report a final checkpoint. The encoded value is what lets later runs resume without forgetting refused files.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 409–410)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This helper updates the set of refused file IDs after a file has been checked. It either keeps the file on the retry list or removes it once it is settled.

**Data flow**: It receives the current refused set, one file ID, and a flag saying whether that file is still refused. If still refused, the ID is added; otherwise it is removed. The result is a new refused set for the next cursor.

**Call relations**: paginate calls this after every listed or retried file. It keeps the carried-refusal checkpoint aligned with what just happened during the sync.

*Call graph*: called by 1 (paginate).


##### `_is_per_file_refusal`  (lines 413–419)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This helper decides whether a Google error means “this one file or tab is not readable” rather than “the whole Google connection is broken.” That distinction lets the connector skip only the blocked item when safe.

**Data flow**: It receives an HTTP status code and parsed Google error details. It returns true only for 403 or 404 errors that have Google API details, are not quota failures, and are not grant-wide failures. Otherwise it returns false so the caller can raise or skip the whole stream as appropriate.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this when Google refuses a metadata or values request. It uses _is_grant_refusal and Google quota classification to avoid mislabeling broader failures as single-file problems.

*Call graph*: calls 1 internal fn (_is_grant_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records); 1 external calls (is_quota_refusal).


##### `_is_grant_refusal`  (lines 422–427)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This helper recognizes errors that mean the user’s Google grant or project setup lacks required access. These are not isolated spreadsheet problems.

**Data flow**: It receives parsed Google error details. It looks for known permission/setup reasons, such as insufficient permissions or API access not configured, and also checks Google service-domain details. It returns true when the error appears to apply to the grant, service, or project as a whole.

**Call relations**: _is_per_file_refusal calls this while classifying refusals. If this helper says the grant is refused, the caller must not treat the error as just one blocked file.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 430–451)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper expands one spreadsheet record into one record per tab. It gives each tab its own stable ID and copies useful parent spreadsheet information onto it.

**Data flow**: It receives a spreadsheet record with a list of Sheets API tab objects. It skips malformed tab entries or tabs without a sheet ID. For each valid tab, it builds a record containing the original tab data plus spreadsheet ID, spreadsheet title, tab title, and timestamps.

**Call relations**: _visit_records calls this for the sheets stream. It turns one spreadsheet visit into the tab-level records that the sync framework can store separately.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 454–467)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This helper builds the record for one tab’s cell values. It wraps Google’s value range with the spreadsheet and tab identity needed by the rest of the system.

**Data flow**: It receives the parent spreadsheet record, a tab title, a sheet ID, and the Sheets API value range. It returns a dictionary with the value range plus a stable ID, spreadsheet details, tab details, and timestamps.

**Call relations**: _sheet_value_records calls this after each successful grid read. It is the final packaging step before cell values are returned to paginate.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 470–472)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This helper formats a tab title as a safe Google Sheets range. It protects names with spaces, special characters, or apostrophes from being misunderstood as cell references or named ranges.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title and wraps the whole title in single quotes. The result is a Sheets A1-style sheet reference for requesting that tab’s values.

**Call relations**: _sheet_value_records calls this before batch and single-tab value requests. It makes sure Google reads the intended tab, not something with a similar-looking name.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 475–480)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This helper turns a grid of cell values into simple readable text. Each row becomes a line, and cells in a row are separated with vertical bars.

**Data flow**: It receives an unknown value. If it is not a list, it returns an empty string. If it is a list of rows, it keeps list-shaped rows, converts each cell to text, joins cells with ` | `, and joins rows with newlines.

**Call relations**: render calls this for sheet_values records. It converts raw spreadsheet rows into the plain text body used for display or recall.

*Call graph*: called by 1 (render).


##### `_str`  (lines 483–484)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely returns a string only when the input is already text. It prevents display code from accidentally showing non-text values as titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render calls this while building titles and short descriptions for spreadsheet and sheet records. It keeps rendering simple and predictable.

*Call graph*: called by 1 (render).


### Microsoft Graph sources
Microsoft Teams and Outlook connectors bridge Microsoft Graph collaboration, messaging, calendar, and contact data into the common stream-sync format.

### `extensions/sources/ufo_ext_sources/providers/microsoft_teams.py`

`io_transport` · `source sync`

Microsoft Teams data is not stored locally here. It lives behind Microsoft Graph, so this connector acts like a careful tour guide: it asks Microsoft for the signed-in user’s joined teams, then visits each team’s channels, then each channel’s messages. It also asks for the user’s chats and then each chat’s messages.

The file defines the streams the system can sync: teams, channels, channel messages, chats, and chat messages. A stream is a named flow of records, like “all channel messages.” For messages, the connector supports incremental syncing: it compares each message’s last modified time with a saved cursor, or watermark, so later runs can skip old messages and fetch only newer changes.

Microsoft Graph returns long lists in pages, with a link to the next page. This connector relies on shared paging support from its parent class to follow those links. It also adds helpful context to child records, such as putting a team ID onto each channel record, so later code knows where the record came from.

The connector is intentionally read-only. It does not post messages or change Teams. If Microsoft refuses access to a whole top-level stream, the connector reports that stream as skipped instead of crashing the entire sync. If just one team, channel, or chat is unavailable, it skips that parent and continues with the rest.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Teams that the signed-in Microsoft account has joined. Other parts of the connector need this list before they can look inside each team for channels and messages.

**Data flow**: It receives an asynchronous HTTP client that can talk to Microsoft Graph. It asks the `/me/joinedTeams` endpoint for teams, follows every returned page, collects all team records into one list, and returns that list.

**Call relations**: This is the first step for team-based data. `MicrosoftTeamsConnector._channels` calls it before fetching channels for each team, and `MicrosoftTeamsConnector.paginate` calls it directly when the requested stream is `teams`.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the channels inside every joined team. It adds the parent team’s identity to each channel, so a channel is not just a loose record but can be traced back to its team.

**Data flow**: It starts with the HTTP client, asks `_teams` for all joined teams, and loops through them. For each team with a usable ID, it requests that team’s channels, adds context such as `team_id` and `team_name` to the channel records, and yields the channel pages one by one. If Microsoft says a particular team is forbidden or missing, it skips that team and keeps going.

**Call relations**: This sits between team discovery and channel message discovery. `MicrosoftTeamsConnector._channel_messages` uses it to know which channels to inspect, while `MicrosoftTeamsConnector.paginate` uses it when syncing the `channels` stream. It hands enriched channel records through `with_context` so downstream code has the parent team information.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every channel the user can reach. It can limit the results to messages changed after a saved cursor, which makes repeat syncs faster and avoids re-reading unchanged messages.

**Data flow**: It receives an HTTP client and an optional cursor string. It asks `_channels` for channel records, takes each channel’s team ID and channel ID, and requests that channel’s messages from Microsoft Graph. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is newer than that cursor. It adds context such as team ID, channel ID, and thread ID, then yields non-empty message pages. If one channel or parent cannot be accessed, it skips that part and continues.

**Call relations**: This is used by `MicrosoftTeamsConnector.paginate` when the system is syncing `channel_messages`. It depends on `_channels` to supply the list of places to search, and it uses `with_context` to attach location information before the records move onward.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s Microsoft Teams chats. This gives the connector the list of private or group chat threads that can later be searched for messages.

**Data flow**: It receives an asynchronous HTTP client, requests `/me/chats` from Microsoft Graph, follows all pages, collects every chat record into a list, and returns that list.

**Call relations**: This is the chat-side starting point, similar to `_teams` for team-side data. `MicrosoftTeamsConnector._chat_messages` calls it before fetching messages from each chat, and `MicrosoftTeamsConnector.paginate` calls it directly for the `chats` stream.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from each Teams chat the user can access. Like channel message syncing, it can use a cursor to fetch only messages changed since the last successful run.

**Data flow**: It receives an HTTP client and an optional cursor. It asks `_chats` for chat threads, skips any chat without a usable ID, and requests messages for each remaining chat. If a cursor is present, it filters out messages whose `lastModifiedDateTime` is not newer. It adds context such as `chat_id` and `thread_id`, then yields only pages that still contain messages. If one chat is forbidden or missing, it skips that chat and continues.

**Call relations**: This is called by `MicrosoftTeamsConnector.paginate` for the `chat_messages` stream. It relies on `_chats` for the list of chat threads and uses `with_context` so each message keeps a clear link to its chat.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the connector’s traffic director. Given a requested stream, it chooses the right helper to fetch that kind of Teams data and yields pages of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and routes the request to `_teams`, `_channels`, `_channel_messages`, `_chats`, or `_chat_messages`. It yields pages of records as they are found. If Microsoft refuses access with an authorization-related error, it turns that into `StreamSkipped`, meaning the run records a skipped stream rather than treating it as a full failure. If the stream name is unknown, it also reports it as skipped.

**Call relations**: The broader source framework calls this when it wants records for one Microsoft Teams stream. This method then calls the specific fetcher for that stream. It also translates certain Microsoft Graph errors into `StreamSkipped` so the surrounding sync process can continue cleanly.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Teams record into a readable title and text body for storage or search. Message records get special treatment because Microsoft Graph stores their body as HTML.

**Data flow**: It receives one record and the stream it came from. For non-message streams, it delegates to the parent connector’s normal rendering. For channel and chat messages, it reads the subject, pulls `body.content` from the nested record, strips HTML tags out of the message body, and returns a plain title plus a simple Markdown-like text page headed with the stream name.

**Call relations**: The source framework calls this after records are fetched and need to become recallable pages. For message streams it calls `_str`, `_strip_html`, and `get_path`; for all other streams it hands the work back to the base `RestConnector` rendering behavior.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a small piece of HTML text into plainer text by removing tags. This is used because Teams message bodies from Microsoft Graph are stored as HTML, not as clean plain text.

**Data flow**: It receives any value. If the value is not a string, it returns `None`. If it is a string, it replaces HTML tags such as `<p>` or `<b>` with spaces, trims extra space from the ends, and returns the cleaned text.

**Call relations**: Only `MicrosoftTeamsConnector.render` calls this helper. It prepares message bodies so the rendered page is readable instead of showing raw HTML markup.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a possible title value into a string title. It avoids accidentally using non-text values as message subjects.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: Only `MicrosoftTeamsConnector.render` calls this helper. It keeps the rendering step simple and predictable when a Teams message has no subject or has a subject in an unexpected form.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/outlook.py`

`io_transport` · `active during Outlook source sync runs`

Outlook data lives behind Microsoft Graph, Microsoft’s web API for mail, calendars, contacts, and related account data. This file turns that API into predictable streams the rest of the system can sync. Without it, the project would not know which Outlook endpoints to call, how to resume from the last sync, or how to translate Microsoft’s records into the project’s common field names.

The main class, OutlookConnector, defines five streams: contacts, messages, conversations, events, and mail folders. Most streams use Graph’s “delta” feed. A delta feed is like asking, “What has changed since the last receipt I gave you?” The connector stores Graph’s next receipt, called a delta link, as the cursor for the next run. For messages and contacts, there can be many folders, so the cursor is a small JSON map from folder ID to that folder’s delta link.

Messages are synced folder by folder. Contacts are synced from the default contact area and any contact folders. Events are synced through a calendar window around the current date. Conversations are not a real Graph stream here; they are built by reading messages and grouping them by conversation ID.

The file also reshapes records into friendlier fields, such as contact email, message snippet, event start time, and plain-text event description. If Graph refuses access with a permission error, the stream is skipped rather than crashing the whole run.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: Formats a Python date and time into the UTC timestamp shape Microsoft Graph expects in filters. It is used when the connector asks Graph for only items after a certain time.

**Data flow**: It receives a datetime value, converts it to UTC, and returns a string like 2024-01-01T12:00:00Z. It does not change any outside state.

**Call relations**: When conversation or message syncing needs a starting time, those flows call this helper before building the Microsoft Graph filter text.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Turns a small piece of HTML into simpler readable text by removing tags. This is useful for event descriptions, which Graph may provide as HTML.

**Data flow**: It receives any value. If the value is not text, it returns None. If it is text, it removes HTML-looking tags, trims extra space, and returns the cleaned string.

**Call relations**: The flattening step calls this when preparing calendar event records, so downstream readers see a plain description instead of raw HTML markup.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address in a Microsoft contact record. Contacts can contain several email slots, but the common output wants one main email field.

**Data flow**: It receives a contact dictionary, looks at its emailAddresses list, and reads each nested emailAddress.address value. It returns the first non-empty email string it finds, or None if there is no usable email.

**Call relations**: OutlookConnector.flatten calls this while reshaping contact records into the project’s friendlier contact format.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from a contact record. It prefers the mobile phone number, then falls back to the first business phone.

**Data flow**: It receives a contact dictionary. It first checks mobilePhone, then scans businessPhones if needed. It returns a phone string or None, and does not modify the record.

**Call relations**: OutlookConnector.flatten calls this for contacts so the normalized record can expose a single simple phone field.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the connector’s public paging entry for the source runner. It accepts the runner’s context and passes the relevant sync request into the Outlook-specific pagination logic.

**Data flow**: It receives an HTTP client, a stream description, the saved cursor, the current user ID if available, and an optional backfill floor. It forwards the stream, cursor, and backfill time to paginate, then yields whatever pages paginate produces.

**Call relations**: The source framework calls this when it wants Outlook records. This method is a thin doorway into OutlookConnector.paginate, keeping the connector compatible with the wider source interface.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right sync routine for the requested Outlook stream. It is the traffic director that sends messages, contacts, events, conversations, and mail folders down their own paths.

**Data flow**: It receives an HTTP client, stream description, saved cursor, and optional backfill time. Based on the stream name, it calls the matching page generator and yields its pages. If Microsoft Graph returns a permission refusal, it raises StreamSkipped so the run records a skip instead of a hard failure.

**Call relations**: paginate_source hands work to this function. It then calls the specialized helpers for contacts, conversations, events, mail folders, or messages; if no known stream matches, it reports that the stream is not implemented.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records by reading messages and grouping them by Microsoft’s conversation ID. This is needed because the connector treats conversations as a stream even though they are derived from messages.

**Data flow**: It receives an HTTP client, a cursor, and an optional backfill time. It builds a Graph query ordered by message modification time, optionally filtering after the cursor or backfill time. As messages arrive, it keeps one record per conversation, preserving the earliest created time and latest updated message. At the end, it yields the collected conversation records.

**Call relations**: OutlookConnector.paginate calls this when the requested stream is conversations. It uses _graph_instant when it needs to turn a backfill date into a Graph-compatible filter.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta endpoint and turns Graph’s response into the project’s StreamPage format. This is the shared engine for streams that can ask Graph, “what changed since last time?”

**Data flow**: It receives an HTTP client, an initial API path, an optional saved cursor, and optional query parameters. It calls Graph page by page, separates normal records from deleted items marked with @removed, and yields StreamPage objects containing records, tombstone delete IDs, and the next cursor. It follows @odata.nextLink until Graph gives the final @odata.deltaLink.

**Call relations**: paginate uses it directly for mail folders, and the message, contact, and event helpers use it for their own delta feeds. It packages each Graph response into StreamPage objects for the sync runner.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs email messages from every mail folder while remembering a separate delta cursor for each folder. This matters because Outlook message changes are tracked per folder.

**Data flow**: It receives an HTTP client, a saved cursor map, and an optional backfill time. It decodes the cursor JSON, lists mail folders, builds a starting delta path for each folder, and optionally adds a received-date filter for the first backfill. For every page, it adds the folder ID to each message, updates that folder’s cursor, re-encodes the cursor map, and yields a StreamPage.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It relies on _list_mail_folders to find folders, _graph_delta_pages to read each folder’s delta feed, _decode_cursor_map and _encode_cursor_map to preserve progress, and _graph_instant to format the backfill date.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs contacts from the default contact area and from any contact folders, keeping separate progress for each one. This lets the next run ask only for contact changes in each folder.

**Data flow**: It receives an HTTP client and a saved cursor map. It decodes the map, builds a list containing the default contact area plus discovered contact folders, and reads each folder’s delta feed. As pages arrive, it updates the folder’s cursor, re-encodes the full cursor map, and yields records and deletes in StreamPage objects. If the default contact delta endpoint is unavailable with a 400 or 404, it skips that default area and continues.

**Call relations**: OutlookConnector.paginate calls this for contacts. It uses _list_contact_folders to discover folders, _graph_delta_pages to read Graph changes, and the cursor map helpers to carry progress across runs.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs calendar events through Microsoft Graph’s calendar-view delta feed. It limits the first request to a practical window around today rather than asking for all calendar history and far-future events.

**Data flow**: It receives an HTTP client and optional cursor. It calculates a time window from one year in the past to two years in the future, then asks _graph_delta_pages to read /me/calendarView/delta with those dates when starting fresh. It yields each StreamPage unchanged.

**Call relations**: OutlookConnector.paginate calls this for the events stream. This helper delegates the actual delta paging to _graph_delta_pages after choosing the calendar time window.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Fetches the IDs of the user’s Outlook mail folders. Message syncing needs these IDs so it can sync each folder’s message delta feed separately.

**Data flow**: It receives an HTTP client, reads pages from the /me/mailFolders endpoint, collects non-empty string IDs, and returns a list of folder IDs.

**Call relations**: _message_delta_pages calls this before message syncing begins, so it knows which folder-specific delta endpoints to walk.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Fetches the IDs of the user’s Outlook contact folders. Contact syncing uses these IDs to include contacts outside the default contact area.

**Data flow**: It receives an HTTP client, reads pages from the /me/contactFolders endpoint, collects valid folder IDs, and returns them as a list.

**Call relations**: _contact_delta_pages calls this before contact syncing begins, then uses the returned IDs to build each folder’s contact delta path.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Reshapes raw Microsoft Graph records into fields that are easier for the rest of the system to use. It keeps the original data while adding common names such as email, snippet, sent_at, title, and start_at.

**Data flow**: It receives a raw record and the stream it belongs to. For contacts, it adds name, email, phone, and created_at fields. For messages, it adds subject, snippet, sender address, sent time, and conversation/thread IDs. For events, it adds title, plain description, start and end times, and location. For other streams, it returns the record unchanged.

**Call relations**: The sync framework calls this after records are fetched and before they are stored or rendered. It uses _first_email, _phone, _strip_html, and nested path lookups to pull useful values out of Microsoft’s record shape.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Reads the saved folder-to-cursor progress map from JSON text. This lets message and contact sync resume each folder from the right place.

**Data flow**: It receives raw cursor text or None. If there is no cursor, invalid JSON, or a non-dictionary value, it returns an empty map. Otherwise, it returns a dictionary of string folder IDs to string cursor links, ignoring empty or non-string cursor values.

**Call relations**: _message_delta_pages and _contact_delta_pages call this at the start of their work so they can recover the per-folder progress from the previous run.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns the folder-to-cursor progress map back into JSON text for storage. This is how the connector remembers where to resume next time.

**Data flow**: It receives a dictionary of folder IDs to cursor links. If the map has entries, it returns a stable JSON string with sorted keys; if it is empty, it returns None.

**Call relations**: _message_delta_pages and _contact_delta_pages call this after updating folder progress, then attach the encoded cursor to the StreamPage they yield.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack workspace source
The Slack connector syncs workspace users, channels, messages, threads, and participants as searchable collaboration records.

### `extensions/sources/ufo_ext_sources/providers/slack.py`

`io_transport` · `source sync runs`

Slack does not send a simple “here is everything that changed” feed for all of these objects, so this connector has to carefully walk the Slack Web API. It lists users and conversations as full snapshots, meaning if something disappears from Slack’s visible results, the system can treat it as deleted or no longer accessible. For messages, it first finds readable conversations, then reads each channel’s message history page by page.

A key complication is pagination: Slack returns results in chunks and gives a cursor, like a bookmark, for the next chunk. Another complication is that Slack often reports errors inside a successful-looking HTTP response, with `ok=false`. This file catches those cases and decides whether the whole stream should be skipped, one channel should be skipped, or the error should stop the run.

The message-reading path is careful about time. Slack history comes newest-first, so the connector uses per-channel progress tracking to avoid a busy channel causing a quiet channel to be skipped. Each raw Slack message can produce up to three kinds of records: a message, a thread summary, and a participant record. Deleted messages are reported as deletes. The connector also avoids importing messages from the current live bot user, so the system does not recall its own Slack surface messages as source material.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error object when Slack says a request failed inside the response body. It keeps Slack’s error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives a Slack error name and optionally a needed permission. It builds a readable error message, stores the error details on the exception, and returns an exception object ready to be raised.

**Call relations**: When `_ok_or_raise` sees Slack return `ok=false`, it calls this constructor. The resulting error then travels upward to code that decides whether the problem is a missing permission, an unreadable channel, or a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard connector entry point for reading pages from Slack. It exists so the wider source framework can ask this connector for one stream without knowing Slack-specific details.

**Data flow**: It receives an HTTP client, a stream description, saved cursor information, the connector’s own Slack user id, and an optional backfill cutoff date. It passes those inputs straight into `paginate` and returns the pages produced there.

**Call relations**: The source framework calls this method when it wants Slack records. This method immediately hands the real work to `SlackConnector.paginate`, keeping the public interface small and consistent with other connectors.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses the right reading strategy for each Slack stream. Users and conversations are simple lists, while messages, threads, and participants require walking channel history.

**Data flow**: It receives the target stream plus cursor and backfill information. For user or conversation streams it yields list pages directly. For message-derived streams it first builds a user lookup and a map of active conversations, then uses `PartitionWalk` to read each channel in safe newest-first slices and yields stream pages.

**Call relations**: It is called by `paginate_source`. It calls `iter_users`, `iter_conversations`, and `user_index` to prepare data, uses `_slack_ts` to convert a date floor into Slack’s timestamp format, and creates a `PartitionWalk` so each channel advances independently. If a stream is unknown, it raises `StreamSkipped` so the run records a skip instead of pretending data exists.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies the list of channel ids that should be walked for message history. Think of it as handing the channel walker one aisle of the Slack archive at a time.

**Data flow**: It reads the already-built channel map from the surrounding `paginate` function. It yields each channel id one by one and does not return a separate final value.

**Call relations**: This helper is passed into `PartitionWalk` by `paginate`. `PartitionWalk` uses it to know which channel partitions exist before asking `channel_pages` to fetch each channel’s history.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects `PartitionWalk` to the Slack channel-history reader. It adapts the partition walker’s request for one channel and one time bound into a call that can fetch Slack history pages.

**Data flow**: It receives a channel id and a time window bound from `PartitionWalk`. It looks up the full conversation details and passes the HTTP client, stream, conversation, bound, user lookup, and self-user id into `_channel_pages`, then returns that async page iterator.

**Call relations**: This helper is created inside `paginate` and handed to `PartitionWalk`. Whenever the walker wants the next slice of one channel, it calls this helper, which delegates to `_channel_pages`.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users in pages and turns them into the project’s normal user shape. It is used both for the user stream itself and as a lookup table for messages.

**Data flow**: It starts with no Slack cursor, asks `/api/users.list` for a page, flattens valid member objects with `_flatten_user`, yields any users found, then follows Slack’s next cursor until there are no more pages.

**Call relations**: It is called directly by `paginate` for the `users` stream and by `user_index` when message processing needs user details. It relies on `_enumerate` for safe Slack GET requests, `_flatten_user` for reshaping records, and `_next_cursor` for page-to-page movement.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including public channels, private channels, group messages, and direct messages. It normalizes Slack’s channel fields into records the rest of the system can understand.

**Data flow**: It asks `/api/conversations.list` for a page with a cursor and conversation type filter. For each valid channel object, it extracts names, type flags, dates, creator, topic, purpose, and membership counts, then yields pages until Slack stops giving a next cursor.

**Call relations**: It is called by `paginate` for the `conversations` stream and again when message-derived streams need the list of readable channels. It uses `_enumerate` to call Slack, `_conversation_type`, `_nested_value`, and `_unix_to_iso` to clean fields, and `_next_cursor` to continue pagination.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table from Slack user id to normalized user record. Message processing uses this to add email addresses and display names to message and participant records.

**Data flow**: It reads pages from `iter_users`, checks each record for a string id, and stores each user under that id in a dictionary. It returns the completed dictionary.

**Call relations**: It is called by `paginate` before reading message-derived streams. It depends on `iter_users`, so it shares the same permission and pagination behavior as the main user stream.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one channel’s Slack message history within the time window requested by the partition walker. It also knows when a single unreadable channel should be skipped without failing the whole Slack sync.

**Data flow**: It receives one conversation, a stream, a time bound, user lookup data, and the connector’s own Slack user id. It builds Slack `conversations.history` parameters, posts them to Slack, filters valid raw messages, converts each page with `_message_page`, yields those walk pages, and follows Slack cursors until the channel is done.

**Call relations**: It is called through the nested `channel_pages` helper inside `paginate`. It calls `_slack_post` to fetch history, `_message_page` to produce stream-specific records, and `_next_cursor` to continue. If Slack refuses that channel with certain errors, it raises `PartitionSkipped`, allowing other channels to keep syncing.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the particular stream being requested: thread summaries, message records, or participant records. It also reports deleted messages for the messages stream.

**Data flow**: It receives raw Slack messages plus conversation and user context. It skips Slack deletion marker bodies except to record deleted message ids, flattens normal messages, derives thread summaries and sender participant records, calculates the newest and oldest Slack timestamps in the page, and returns a `WalkPage` containing the chosen records.

**Call relations**: It is called by `_channel_pages` for each Slack history page. It calls `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message` to derive the three possible record types, then packages the result for `PartitionWalk` using `WalkPage`.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes top-level Slack listing calls safer by turning missing-permission responses into stream skips. This prevents a missing Slack scope from looking like a broken sync.

**Data flow**: It receives an HTTP client, API path, and query parameters. It calls `_slack_get`; if Slack reports a known permission refusal or returns HTTP 403, it raises `StreamSkipped`; otherwise it returns the response data or lets unexpected errors rise.

**Call relations**: It is used by `iter_users` and `iter_conversations`, the two top-level enumeration readers. It calls `_slack_get`, and on known permission problems it creates `StreamSkipped` so the wider run can record a skipped stream.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a Slack GET request and checks Slack’s own success flag. It hides the odd Slack pattern where a failed API call may still arrive as an HTTP success.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It delegates the network request to the base connector’s `_get`, passes the returned JSON-like data through `_ok_or_raise`, and returns only confirmed-success data.

**Call relations**: It is called by `_enumerate`. It relies on `_ok_or_raise` to convert Slack `ok=false` bodies into `SlackApiError` exceptions.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a Slack POST request and checks Slack’s own success flag. It is used for Slack endpoints, such as conversation history, that are read with POST-shaped requests.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It delegates to the base connector’s `_post`, checks the response with `_ok_or_raise`, and returns the successful response data.

**Call relations**: It is called by `_channel_pages` while reading `conversations.history`. Like `_slack_get`, it hands Slack body-level errors to `_ok_or_raise`.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether a Slack response body says the request actually succeeded. Slack may return HTTP 200 while still saying `ok=false`, so this function is the guardrail.

**Data flow**: It receives parsed Slack response data. If `ok` is exactly false, it extracts the error code and optional missing scope, raises `SlackApiError`, and otherwise returns the original data unchanged.

**Call relations**: It is called by both `_slack_get` and `_slack_post` after network requests. When it creates `SlackApiError`, higher-level code such as `_enumerate` or `_channel_pages` decides whether to skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s bookmark for the next page of results. Without it, the connector would only read the first page of users, channels, or messages.

**Data flow**: It receives a Slack response dictionary, looks inside `response_metadata.next_cursor`, and returns that cursor only if it is a non-empty string. If there is no usable cursor, it returns `None`.

**Call relations**: It is called by `iter_users`, `iter_conversations`, and `_channel_pages` after each Slack page. Those loops use its result to decide whether to make another API call or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack-style Unix time, which is seconds since 1970, into an ISO timestamp string that is easier for the rest of the system to store and compare. It quietly rejects values that are not real timestamps.

**Data flow**: It receives any value. If the value can be read as a number of seconds, it converts it to a UTC ISO date-time string; if not, or if the value is a boolean, it returns `None`.

**Call relations**: It is used by `iter_conversations` for channel creation dates and by `_flatten_user` for user update times. It calls Python’s datetime conversion to do the actual timestamp formatting.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a normal date-time into Slack’s special message timestamp string format. This matters because message-history walking compares Slack timestamps as strings, so the width must be stable.

**Data flow**: It receives an optional date-time. If there is no date, it returns `None`; if the date is before the Unix epoch, it also returns `None`; otherwise it returns a zero-padded seconds-with-microseconds string shaped like Slack message timestamps.

**Call relations**: It is called by `paginate` when setting the oldest backfill boundary for message-derived streams. The formatted value is passed into `PartitionWalk` as the floor for channel history reading.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack’s message timestamp string into a normal UTC ISO date-time string. This makes message and thread times usable outside Slack-specific code.

**Data flow**: It receives a Slack timestamp string or `None`. If the value is empty or cannot be parsed as a number, it returns `None`; otherwise it converts the timestamp to an ISO date-time string.

**Call relations**: It is used by `_flatten_message` for sent times and by `_conversation_thread_from_message` for thread activity times. It calls Python’s datetime conversion for the final formatting.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns one raw Slack user object into the simpler user record shape used by this system. It picks useful identity fields such as email, display name, real name, bot status, and deletion status.

**Data flow**: It receives a raw Slack member dictionary. It reads the nested profile when present, cleans and lowercases email, chooses the best display names using `_first_text`, converts the update timestamp with `_unix_to_iso`, and returns a new normalized dictionary.

**Call relations**: It is called by `iter_users` for every valid Slack member. It depends on `_first_text` to choose the first meaningful name and `_unix_to_iso` to normalize Slack’s update time.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into a normalized message record. It also filters out messages sent by the connector’s own live bot user so the system does not ingest its own output.

**Data flow**: It receives a raw message, the conversation it came from, a user lookup table, and the connector’s own user id. It validates the timestamp and channel, skips self-user messages, finds sender information, builds ids for the message and thread, converts the sent time, creates a short snippet, and returns the normalized message dictionary or `None` if the message should not be used.

**Call relations**: It is called by `_message_page` while deriving records from Slack history. It uses `_slack_ts_to_iso` for time conversion, `_snippet` for a compact preview, and `_first_text` to choose the best sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Derives a thread summary from a message when that message is either the root of a thread or a reply inside one. Messages that are not part of a thread do not produce a thread record.

**Data flow**: It receives a normalized message, the raw Slack message, and conversation details. It checks thread ids and reply counts, chooses the latest known activity timestamp, counts participants when Slack provides reply users, and returns a thread summary dictionary or `None`.

**Call relations**: It is called by `_message_page` after each message is flattened. It uses `_slack_ts_to_iso` to turn Slack timestamps into normal dates for creation, update, and last-message fields.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system connect messages to people or bots in a searchable way.

**Data flow**: It receives a normalized message and the user lookup table. It chooses a sender handle, preferably email and then Slack user id, and if one exists it returns a participant dictionary tied to the message, channel, and thread; otherwise it returns `None`.

**Call relations**: It is called by `_message_page` for each flattened message. It uses `_first_text` to choose the best sender handle before the participant record is added to the page.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Labels a Slack conversation as a direct message, multi-person direct message, private channel, or public channel. This gives downstream code a simple category instead of several Slack boolean flags.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s type flags in priority order and returns one plain string describing the conversation type.

**Call relations**: It is called by `iter_conversations` while normalizing channel records. The returned value becomes the `conversation_type` field used later by messages and thread records.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value from inside nested dictionaries, such as Slack’s topic or purpose text. It avoids crashes when Slack leaves part of the nested structure out.

**Data flow**: It receives a starting dictionary and a path of keys. It walks one key at a time; if the current value stops being a dictionary, it returns `None`, otherwise it returns the final nested value.

**Call relations**: It is called by `iter_conversations` to extract fields like `topic.value` and `purpose.value` from Slack channel objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from a list of candidates. It is a small helper for picking the best available name, handle, or email.

**Data flow**: It receives any number of values. It checks them in order, returns the first string that still has text after trimming whitespace, and returns `None` if none qualify.

**Call relations**: It is used by `_flatten_user`, `_flatten_message`, and `_participant_for_message`. Those functions pass fallback choices in preferred order, and this helper picks the first usable one.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short, clean preview of message text. This is useful for search results or thread titles where the full Slack message may be too long.

**Data flow**: It receives optional text. If there is no text it returns `None`; otherwise it collapses repeated whitespace into single spaces, trims the result to the configured snippet length, and returns the preview.

**Call relations**: It is called by `_flatten_message` when building the normalized message record. That snippet may later be reused by `_conversation_thread_from_message` as a thread title or preview.

*Call graph*: called by 1 (_flatten_message).

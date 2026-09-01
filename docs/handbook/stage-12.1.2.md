# Google, Microsoft, and Chat Workspace Connectors  `stage-12.1.2`

This stage is part of the system’s data-gathering work loop. It connects to workplace tools and turns emails, chats, meetings, documents, calendars, and files into records the rest of the system can store, search, and recall later. The Google helper separates two different API refusals: “you do not have permission” and “try again later because the quota is full.” That lets the system skip only what it truly cannot read.

The Gmail, Google Calendar, Docs, Drive, Meet, and Sheets connectors each read one Google workspace area. Gmail formats messages, Calendar reads events and attendees, Docs extracts document text, Drive reads files plus details like comments and permissions, Meet gathers transcripts and notes, and Sheets reads spreadsheet tabs and rows.

On the Microsoft side, Outlook reads mail, contacts, folders, conversations, and calendar changes, while Teams reads teams, channels, chats, and messages through Microsoft Graph, Microsoft’s API gateway. Slack does the same for Slack users, channels, messages, threads, and authors. Together, these connectors act like adapters, making many different services look like one steady stream of searchable content.

## Files in this stage

### Google API Foundations
Shared Google connector logic distinguishes permission failures from retryable quota limits before individual Google workspace streams run.

### `extensions/sources/ufo_ext_sources/providers/google.py`

`domain_logic` · `source sync error handling`

Google often reports very different problems with the same HTTP status codes, especially 401 and 403. In plain terms, both can mean “no,” but one kind of “no” means “this account did not grant enough permission,” while another means “you have used too much of today’s allowance.” This file separates those cases.

It looks inside the error message returned by Google. If the message says the status is RESOURCE_EXHAUSTED, or if one of Google’s detailed error reasons mentions rate or quota limits, the file treats it as a quota problem. That kind of problem is temporary, so the connector should let the run fail and rely on retry and backoff, meaning the system waits before trying again.

If the status is 401 or 403 and it is not a quota problem, the file treats it as a permission problem. That is considered settled: trying again will not add the missing access scope. The caller can then skip that stream rather than repeatedly failing.

The important behavior is protective. It avoids parking or skipping streams just because Google’s quota window has not reset yet, while still avoiding pointless retries for permissions the user never granted.

#### Function details

##### `error_detail`  (lines 31–38)

```
def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This function pulls the main Google error object out of an HTTP error response. If the response is not valid JSON, or does not contain the expected shape, it safely returns an empty dictionary instead of crashing.

**Data flow**: It receives an httpx HTTPStatusError, reads the response body, and tries to parse it as JSON. It then looks for the nested error field and uses dict_or_empty to make sure the result is really a dictionary. The output is the Google error detail dictionary, or an empty dictionary when that detail is missing or unreadable.

**Call relations**: When refused_for_scope needs to decide why Google refused a request, it calls error_detail first to extract the useful part of the response. error_detail relies on the shared dict_or_empty helper so later code can inspect the result without worrying about unexpected response shapes.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (dict_or_empty).


##### `is_quota_refusal`  (lines 41–45)

```
def is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This function decides whether a Google refusal is really about a usage limit, such as a daily quota or rate limit. That tells the caller that the problem may clear after waiting.

**Data flow**: It receives the parsed Google error detail as a dictionary. First it checks whether the top-level status is RESOURCE_EXHAUSTED. If not, it looks through the detailed errors list, safely treating missing or malformed lists as empty, and checks each reason against known Google quota reason names. It returns true for quota-related refusals and false otherwise.

**Call relations**: refused_for_scope calls is_quota_refusal after extracting the error detail. This function provides the key distinction that prevents quota failures from being mistaken for permanent permission failures.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (list_or_empty).


##### `refused_for_scope`  (lines 48–53)

```
def refused_for_scope(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This function answers the main question for the connector: did Google refuse because the granted account permission is not enough? It returns true only for 401 or 403 errors that are not quota-limit errors.

**Data flow**: It receives an httpx HTTPStatusError, checks the response status code, and only considers Google refusal codes 401 and 403. For those responses, it calls error_detail to read the body and is_quota_refusal to rule out temporary quota problems. The output is a boolean: true means the stream can be skipped for missing permission; false means the error should not be treated that way.

**Call relations**: This is the function other connector code would call when handling a failed Google API request. It ties together error_detail and is_quota_refusal so callers get one simple yes-or-no answer instead of having to understand Google’s error format themselves.

*Call graph*: calls 2 internal fn (error_detail, is_quota_refusal).


### Google Mail and Calendar
Gmail and Google Calendar connectors ingest personal communication and scheduling records, including messages, events, and attendees.

### `extensions/sources/ufo_ext_sources/providers/gmail.py`

`io_transport` · `source sync and rendering`

Gmail does not hand over an email as one simple text field. A message is a nested MIME tree, meaning its readable parts may be buried inside several layers, with plain text and HTML stored as encoded chunks. This file is the translator between Gmail’s API and the project’s source-sync system.

On the first run, it backfills messages from a pinned time window, usually the last 30 days unless configured otherwise. It records Gmail’s history marker, called a historyId, so later runs do not scan the whole mailbox again. After that, it asks Gmail only for changes since the last historyId: which messages appeared and which were deleted. Deleted messages become tombstones so the rest of the system can remove or hide them.

For each message that still exists, the connector fetches the full body, pulls out useful headers like From, To, Cc, and Subject, decodes plain text or HTML bodies, and flattens everything into a simple record. Its render path then turns that record into a clean email-like page with a title, headers, and body text. If Gmail refuses access because the connected account lacks the needed read permission, the stream is skipped rather than treated as a broken run.

#### Function details

##### `GmailConnector.paginate_source`  (lines 94–104)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s entry point for syncing Gmail messages from the source runner. It passes along the saved cursor and the backfill cutoff so Gmail can be read from the right point in time.

**Data flow**: It receives an HTTP client, a stream description, an optional cursor, the current user id, and an optional backfill date. It ignores the user id because Gmail uses the authenticated “me” mailbox, then forwards the meaningful inputs to the main pagination routine. The output is an asynchronous stream of pages of records and deletes.

**Call relations**: The source framework calls this when it wants Gmail data. This function immediately hands the work to GmailConnector.paginate, which does the real choice between first-time backfill and later history-based syncing.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 106–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Gmail messages. It decides whether to do an initial mailbox scan or a smaller change scan, fetches message bodies, and yields pages the rest of the system can write.

**Data flow**: It receives a stream, an HTTP client, an optional saved cursor, and an optional backfill date. With no cursor, it gathers message ids from the backfill window; with a cursor, it asks Gmail for additions and deletions since that history point. It then fetches full message bodies in chunks and yields StreamPage objects containing records, delete ids, and the next cursor.

**Call relations**: GmailConnector.paginate_source calls this during source sync. It calls _backfill for first runs, _history for later runs, and _fetch_bodies to turn message ids into full records. If Gmail says the account lacks the needed read scope, it raises StreamSkipped so the run records a clean skip instead of a hard failure.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 2 external calls (__init__, refused_for_scope).


##### `GmailConnector._backfill`  (lines 145–170)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time scan of a mailbox window. It lists message ids from Gmail and chooses a history marker so future runs can switch to incremental changes.

**Data flow**: It receives an HTTP client and an optional earliest allowed date. It first reads the mailbox profile’s history id as a safe floor, then lists Gmail message ids page by page, adding an after: timestamp query when a backfill date exists. It returns the collected ids and the cursor that should be saved for the next run.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor yet. It relies on _profile_history_id to read the mailbox’s current history marker and _seed_history_id to choose the best cursor after the listing is complete.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 172–175)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail’s current mailbox history marker from the profile endpoint. That marker is used as a safe starting point when a backfill window contains no messages.

**Data flow**: It receives an HTTP client, asks Gmail for the mailbox profile, and looks for a string historyId field. It returns that string when present, or None when Gmail does not provide it in the expected shape.

**Call relations**: GmailConnector._backfill calls this before listing messages. Reading it before the scan matters because it prevents a newly delivered message from being skipped if the backfill window itself was empty.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 177–201)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the history marker that the next sync should start from. It prefers the newest message found during backfill, but falls back to the earlier mailbox profile marker when needed.

**Data flow**: It receives an HTTP client, the list of message ids found during backfill, and a fallback history id. If there are messages, it fetches the newest listed message in minimal form and returns its historyId when available. If that message has disappeared or no messages were found, it returns the fallback floor.

**Call relations**: GmailConnector._backfill calls this after it finishes listing ids. Its result becomes the cursor that lets GmailConnector.paginate use the faster history path on the next run instead of repeating the same backfill forever.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 203–238)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log since a saved history id. It finds which messages were added and which were deleted so the system can update its stored copy of the mailbox.

**Data flow**: It receives an HTTP client and a starting history id. It asks Gmail for history pages, collects message ids from messageAdded and messageDeleted entries, keeps the latest returned history id, and removes messages that were both added and deleted from the add list. It returns added ids, deleted ids, and the next cursor.

**Call relations**: GmailConnector.paginate calls this when a saved cursor exists. It uses _message_ids to extract ids from Gmail’s history entries. If Gmail returns 404 because the history id is too old, it raises CursorExpired so the larger sync system knows to refetch from scratch.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 240–254)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns Gmail message ids into full message records. It fetches each message body and skips ids that vanished between the change listing and the body fetch.

**Data flow**: It receives an HTTP client and a list of message ids. For each id, it asks Gmail for the full message; if Gmail says that one message no longer exists, it moves on. Each successful raw Gmail response is flattened into a simpler dictionary, and the function returns the list of those records.

**Call relations**: GmailConnector.paginate calls this after it has decided which message ids are newly present. It hands every successful raw message to _flatten_message so the rest of the sync pipeline sees a predictable record shape.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 256–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a flattened Gmail record into readable prose. It makes a synced email look like an email, with a title, sender, recipients, subject, and body, instead of a dump of Gmail’s raw data.

**Data flow**: It receives one record and its stream description. For Gmail messages, it chooses a title from the subject when possible, formats contact headers, chooses the best body text, and returns a pair of title and rendered text. For other streams, it falls back to the parent connector’s default rendering.

**Call relations**: The source system uses this when it needs displayable or recallable text for a synced record. It calls _str, _format_contact, _format_recipients, and _message_body to turn the flat record’s pieces into a human-readable email page.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 279–288)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts message ids from Gmail history entries. It protects the rest of the code from Gmail entries that are missing fields or have an unexpected shape.

**Data flow**: It receives any value that should be a list of history entries. It walks the entries, keeps only dictionary-shaped items with a nested message id string, and returns the clean list of ids.

**Call relations**: GmailConnector._history calls this while reading added and deleted change records from Gmail. The helper keeps the history loop focused on change tracking instead of repeated shape checking.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 291–318)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Gmail message into the simple record format the sync system stores. It pulls out the useful headers, addresses, labels, direction, and decoded body text.

**Data flow**: It receives Gmail’s raw message dictionary. It reads selected headers from the payload, extracts plain-text and HTML bodies, parses sender and recipient addresses, collects labels, and decides whether the message is inbound or outbound. It returns a flat dictionary with stable fields such as id, subject, from_handle, to, labels, body_text, and body_html.

**Call relations**: GmailConnector._fetch_bodies calls this after downloading each full message. It calls _extract_bodies for the nested MIME content, _parse_first_address for the sender, and _addresses for recipient lists.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 321–335)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail message’s nested payload for readable body parts. It returns the first plain-text body and the first HTML body it can find.

**Data flow**: It receives the payload dictionary from a Gmail message. It walks through the payload and its child parts, decodes text/plain and text/html data when found, and stores only the first body of each kind. It returns a pair: plain text or None, and HTML text or None.

**Call relations**: _flatten_message calls this while building a flat message record. Its inner walk routine does the tree traversal and calls _b64url_decode when it finds encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 325–332)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive helper that walks through the nested MIME parts of a Gmail payload. Think of it like opening folders inside folders until it finds the text pages inside.

**Data flow**: It receives one payload part. It checks that part’s MIME type and encoded body data; if it is the first plain-text or HTML body of that type, it decodes and saves it. Then it repeats the same process for each child part.

**Call relations**: It exists inside _extract_bodies and is used during body extraction. When it finds encoded body text, it hands that encoded string to _b64url_decode so the stored result is normal readable text.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 338–344)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body text encoding into normal text. Gmail uses URL-safe base64, which is a compact text-safe way to carry bytes through an API.

**Data flow**: It receives an encoded string from Gmail. It adds any missing padding characters needed by the decoder, decodes the data as UTF-8 text, and replaces invalid characters instead of crashing. If the encoded value is badly formed, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this whenever it finds a plain-text or HTML body part. It uses Python’s base64.urlsafe_b64decode to do the actual decoding.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 347–354)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses the first email address from a header such as From. It separates the mailbox address from the display name.

**Data flow**: It receives a header string or None. If there is no header, it returns two None values. Otherwise it asks the email library to parse addresses, takes the first one, lowercases the address, and returns address plus display name.

**Call relations**: _flatten_message calls this for the sender field. It delegates the tricky address parsing to email.utils.getaddresses, which understands common email header formats like “Name <person@example.com>”.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 357–364)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses a recipient header into a list of address objects. It is used for fields like To and Cc, where there may be several people.

**Data flow**: It receives a header string or None. With no header, it returns an empty list. Otherwise it parses all addresses, lowercases each email address, keeps the display name when present, and returns a list of dictionaries with handle and display_name.

**Call relations**: _flatten_message calls this when flattening To and Cc headers. Like _parse_first_address, it relies on email.utils.getaddresses for the actual email-header parsing.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 367–372)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable label. It produces either “Name <email>” or just the email address.

**Data flow**: It receives a possible email handle and possible display name. If the handle is missing or not a string, it returns an empty string. If a display name exists, it combines name and address; otherwise it returns the address alone.

**Call relations**: GmailConnector.render calls this for the sender line. _format_recipients also calls it for each recipient so all contact display rules stay consistent.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 375–382)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient records for a To or Cc line. It joins multiple people into one comma-separated string.

**Data flow**: It receives a value that should be a list of recipient dictionaries. If it is not a list, it returns an empty string. Otherwise it formats each dictionary as a contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this when building To and Cc headers. It calls _format_contact for each individual recipient so names and addresses are displayed the same way as the sender.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 385–393)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for a message. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail’s snippet if no full body is available.

**Data flow**: It receives a flattened message record. It first checks body_text and returns the trimmed text if present. If not, it checks body_html and converts it into plain readable text. If neither exists, it returns the trimmed snippet or an empty string.

**Call relations**: GmailConnector.render calls this while composing the final email-like text. It is the last step that decides what body a person will actually see or recall.

*Call graph*: called by 1 (render).


##### `_str`  (lines 396–397)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns an unknown value into a string only when it already is one. It avoids accidentally displaying non-text values as subjects or titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this when reading the subject. That keeps the rendering path simple and prevents unexpected data shapes from leaking into the title.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 404–406)

```
def __init__(self) -> None
```

**Purpose**: This prepares the small HTML-to-text parser used for email bodies. It creates a place to collect readable text as the parser moves through HTML.

**Data flow**: It receives no outside data beyond the new parser instance. It initializes the base HTML parser with automatic character reference conversion, then starts an empty list of text parts. The result is a parser ready to be fed HTML.

**Call relations**: _HtmlText.extract creates this parser when HTML content needs to be converted. The parser’s later callbacks add text and line breaks into the parts list.


##### `_HtmlText.extract`  (lines 409–414)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain text. It removes tags and attributes, keeps the visible words, and preserves useful line breaks around block-like elements.

**Data flow**: It receives raw HTML text. It creates a parser, feeds the HTML into it, joins the collected pieces, collapses extra spaces on each line, removes empty lines, and returns clean plain text.

**Call relations**: This is the class-level doorway for using _HtmlText. It relies on the parser callbacks handle_data, handle_starttag, and handle_endtag to collect words and insert line breaks while the HTML is being read.


##### `_HtmlText.handle_data`  (lines 416–417)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records visible text found inside HTML. It is called by the HTML parser whenever it reaches character data between tags.

**Data flow**: It receives a piece of text from the HTML parser. It appends that text to the parser’s internal parts list. Nothing is returned; the parser instance is changed by adding more collected text.

**Call relations**: _HtmlText.extract feeds HTML into the parser, and the parser calls this as it finds readable text. Those collected pieces are later joined into the final plain-text body.


##### `_HtmlText.handle_starttag`  (lines 419–421)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag represents a block boundary, such as a paragraph or list item. That keeps separate ideas from running together after tags are removed.

**Data flow**: It receives a tag name and its attributes from the HTML parser. If the tag is one of the known block-style tags, it appends a newline marker to the parts list; otherwise it does nothing. It returns no value.

**Call relations**: _HtmlText.extract uses the parser that triggers this callback while reading HTML. The newlines it adds are later cleaned and preserved in the extracted plain text.


##### `_HtmlText.handle_endtag`  (lines 423–425)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a closing HTML tag ends a block of content. It helps the final plain text keep paragraph-like separation.

**Data flow**: It receives a closing tag name from the HTML parser. If that tag is one of the known block tags, it appends a newline marker to the internal parts list. It returns no value and only changes the parser’s collected text.

**Call relations**: _HtmlText.extract uses the parser that triggers this callback while reading HTML. Together with handle_starttag and handle_data, it shapes the HTML body into readable plain text.


### `extensions/sources/ufo_ext_sources/providers/googlecalendar.py`

`io_transport` · `source sync`

This connector is the bridge between Google Calendar and the rest of the source-sync system. Its job is read-only: it never changes the calendar. It asks Google for events from the user's primary calendar, reshapes the raw Google data into the project's simpler record format, and sends those records onward to be stored and recalled later.

The connector works like a notebook that remembers where it stopped reading. On the first run, it looks back 90 days and asks Google for events, including deleted ones. Google returns a sync token, which is like a bookmark. Later runs send that bookmark back to Google so only changed or cancelled events need to be fetched.

There are two views of the same calendar data. The main `calendar_events` stream stores one record per event, including title, time, location, description, organizer, and a folded-in attendee list. The `event_attendees` stream splits the attendee list apart, making one row per invited person so attendee information can be queried separately.

The file also knows how to react to common Google API problems. If the bookmark has expired, it asks the wider sync system to restart fresh. If the user did not grant Calendar permission, it marks this stream as skipped rather than treating the whole run as broken.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 51–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches Google Calendar events page by page and turns them into sync pages for either events or attendees. It is the main reading loop for this connector.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. If there is a cursor, it asks Google for changes since that point; if not, it asks for events from the recent lookback window. For each Google response, it separates active records from cancelled events, converts active events into the right local shape, and outputs `StreamPage` objects containing records, deletes, and eventually the next cursor. If Google says the cursor is too old, it raises a cursor-expired signal; if Calendar permission is missing, it raises a skipped-stream signal.

**Call relations**: During a source sync, the framework calls this method to read the selected stream. When building the event stream it hands each active event to `_flatten_event`; when building the attendee stream it hands events to `_flatten_attendees`. It also asks Google's shared helper whether an HTTP refusal was caused by missing permission, so the larger sync run can record a clean skip instead of a hard failure.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 5 external calls (__init__, __init__, now, timedelta, refused_for_scope).


##### `GoogleCalendarConnector.render`  (lines 111–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced calendar event into human-readable text for recall or search. It gives the system a useful title and body instead of leaving the record as raw fields.

**Data flow**: It receives a record and the stream it belongs to. For `calendar_events`, it pulls out the title, time range, location, attendee handles, and description, then formats them as plain text. For other streams, it falls back to the parent connector's normal rendering. The output is a pair: a short title and a longer text body.

**Call relations**: The sync or indexing layer calls this when it needs readable content from a stored record. It uses `_str` to safely turn a possibly missing or non-text title into a string before building the rendered text.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 139–166)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the project's event record shape. This keeps the rest of the system from needing to understand Google's detailed event format.

**Data flow**: It receives one event dictionary from Google. It reads fields such as id, created time, updated time, summary, description, location, start and end, organizer, recurrence id, and attendees. It normalizes attendee email addresses, converts start and end values into consistent timestamp strings, and returns one flat event record with a nested attendee list.

**Call relations**: The main pagination loop calls this for each non-cancelled event in the `calendar_events` stream. Inside, it uses `_attendee` to simplify each attendee and `_parse_when` to normalize Google's two different time formats.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 169–175)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Simplifies one Google attendee object into the small attendee shape stored inside an event record. It focuses on who the attendee is and how they responded.

**Data flow**: It receives a Google attendee dictionary. It lowercases the attendee email into a stable handle, keeps the display name if present, maps Google's response status into the local response wording, and returns that small attendee dictionary.

**Call relations**: `_flatten_event` calls this while building the attendee list that is embedded inside each calendar event record. It does not fetch anything itself; it only reshapes one attendee at a time.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 178–204)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one Google Calendar event into separate attendee records, one per invited person. This supports the separate attendee stream, where invitees can be stored and queried as their own rows.

**Data flow**: It receives one raw Google event. It reads the event id, timestamps, organizer, and attendee list. For every attendee with an email address, it builds a record whose id combines the event id and attendee handle, adds the attendee's role, response, display name, and whether the attendee is the signed-in user, then returns the list of attendee records.

**Call relations**: The pagination loop calls this when syncing the `event_attendees` stream. For each attendee row, it calls `_attendee_role` to label the person as organizer, resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 207–214)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what kind of participant an attendee is for an event. This turns several Google-specific flags into one clear local role.

**Data flow**: It receives an attendee dictionary and a separate flag saying whether this attendee matches the organizer. It checks organizer markers first, then whether the attendee is a resource such as a room, then whether they are optional. It returns one role string: `organizer`, `resource`, `optional`, or `required`.

**Call relations**: `_flatten_attendees` calls this while creating one attendee-stream row per invitee. The returned role becomes part of that attendee record.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 217–226)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google's event time field into one timestamp string format. Google represents timed events and all-day events differently, so this helper hides that difference.

**Data flow**: It receives a value that may be a Google time dictionary. If it contains `dateTime`, it returns that timestamp as text. If it contains an all-day `date`, it turns that date into a midnight UTC timestamp. If the value is missing or not in an expected shape, it returns nothing.

**Call relations**: `_flatten_event` calls this for both event start and event end values. That lets synced event records use a consistent `starts_at` and `ends_at` field even though Google sends different shapes.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 229–230)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents rendering code from accidentally treating missing or non-text data as a title.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `GoogleCalendarConnector.render` calls this when preparing the event title. It is a small guardrail used during human-readable rendering.

*Call graph*: called by 1 (render).


### Google Drive and Document Content
Google Drive, Docs, Meet, and Sheets connectors discover workspace files and meeting artifacts and convert them into searchable text records.

### `extensions/sources/ufo_ext_sources/providers/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Google Docs. Its job is to ask Google Drive, “Which Google Docs can this account see?”, then ask Google Docs for the full contents of each one. Without this file, Google Docs would not become readable source material inside the system.

The work happens in two steps. First, it lists files through the Google Drive API, filtering for real Google Docs, ignoring trashed files, and using a saved time marker so later syncs only fetch documents changed since the last run. This is like checking a mailbox only for letters newer than the last one you opened. Second, for each file ID it finds, it fetches the full document from the Google Docs API.

The connector is careful about partial failure. If Drive says the whole account does not have permission, the stream is skipped with a clear reason. But if one individual document is listed and then cannot be opened, the sync keeps going and records a small placeholder instead of failing everything.

Google Docs store text in a nested structure, not as one simple string. The render path walks through that structure, pulls out paragraph text runs in order, and produces a plain prose version with a simple heading.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 51–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Google Docs in batches so the sync system can process them a page at a time. It combines Drive file metadata, such as name and modified time, with the full document content from the Docs API.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the last synced update time. It asks `_iter_doc_files` for Drive file pages, fetches each document with `_document`, builds a normalized record for each one, and yields lists of records once a batch is large enough. If Google refuses access because the grant lacks the needed permission, it turns that into a skipped stream; otherwise errors continue upward.

**Call relations**: This is the main read loop for the connector. It calls `_iter_doc_files` to discover candidate files, calls `_document` to fetch each full document, and uses Google permission-checking helper logic to decide whether an HTTP failure should skip the stream instead of crashing the sync.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files); 1 external calls (refused_for_scope).


##### `GoogleDocsConnector._document`  (lines 89–98)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: Fetches one Google Doc by its file ID from the Google Docs API. It protects the larger sync from failing just because one listed document cannot be opened or has disappeared.

**Data flow**: It receives an HTTP client and a Google file ID. It requests the full document data from Google Docs and returns that data as a dictionary. If Google responds that the specific document is forbidden or not found, it returns a minimal placeholder containing only the document ID; other errors are passed upward.

**Call relations**: `paginate` calls this after Drive has reported a document file. Its result is folded into the final record that the sync system receives, alongside Drive metadata such as title, URL, and timestamps.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 100–127)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Lists Google Doc files visible to the connected account, one Drive page at a time. It uses the cursor to avoid re-reading old documents when only newer changes are needed.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for non-trashed Google Docs, adds a “modified after this time” filter when a cursor exists, requests pages from Drive, safely turns the returned `files` value into a list, yields non-empty file pages, and follows `nextPageToken` until there are no more pages.

**Call relations**: `paginate` relies on this function as the discovery step. This function talks to Drive and hands back file metadata pages; `paginate` then uses each file ID to fetch the actual document body.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 129–134)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced Google Docs record into a readable text block for recall or indexing. It adds a simple heading so the source and document title are clear.

**Data flow**: It receives one record and the stream it came from. It reads the title if present, asks `_plain_text` to extract the document body, builds a heading, and returns both the title and the final plain-text rendering.

**Call relations**: This is used after records have been fetched, when the system needs a human-readable version of the document. It delegates the hard part of digging text out of Google’s nested document structure to `_plain_text`.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 137–153)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: Extracts the visible paragraph text from a Google Docs document record. Google stores document content as nested objects, so this helper flattens the paragraph text into one normal string.

**Data flow**: It receives a document record. It looks inside `body.content`, walks through paragraph elements, collects each text run’s content in order, joins the pieces together, trims extra whitespace at the ends, and returns the resulting plain text. Non-paragraph structures, such as tables or section breaks, are ignored if they do not contain paragraph text in this shape.

**Call relations**: `render` calls this when creating the final readable version of a record. It does not fetch anything itself; it only translates already-fetched Google Docs data into plain prose.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledrive.py`

`io_transport` · `source sync`

This connector is the bridge between the project and Google Drive. Without it, the system would not know how to ask Google Drive for a user’s files, how to keep that list up to date, or how to notice that a file was deleted or moved to trash.

The main idea is simple: Google Drive gives data in pages, like a long book split into chapters. This file keeps asking for the next page until there are no more pages. On the first run, it lists all untrashed files and then saves a special Google token that marks “where we left off.” On later runs, it uses that token to ask only for changes. If Google says the token is too old, the connector raises a clear “cursor expired” signal so the larger system can start fresh.

It also reads shared drives from scratch each time, and for each file it can fetch smaller related lists: permissions, comments, and revisions. If the user’s Google authorization does not include Drive access, the stream is marked as skipped instead of failed. Finally, the file includes a render step that turns a Drive file record into a short human-readable text block with its name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 87–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Google Drive syncing. Given a requested stream, such as files or comments, it chooses the right helper to fetch that kind of data from Google.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor that says where the last sync stopped. It checks the stream name, calls the matching paging helper, and yields batches of records or page objects back to the sync engine. If Google refuses access because the grant lacks Drive permission, it changes that into a clean skipped-stream result instead of treating it as a broken run.

**Call relations**: The wider sync system calls this when it wants records from Google Drive. For file streams it hands off either to Google Drive change reading or full file listing, then asks for a fresh start token. For shared drives and child streams it delegates to the matching helper. It also uses the Google refusal checker to decide when an authorization problem should become a skipped stream.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 2 external calls (__init__, refused_for_scope).


##### `GoogleDriveConnector._paginate_files`  (lines 121–146)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists Google Drive files page by page. It is used when the connector needs a full view of live, untrashed files.

**Data flow**: It receives an HTTP client and an optional time cursor. It builds a Google Drive search query for untrashed files, optionally newer than the cursor, asks Google for one page at a time, cleans the returned files into a safe list, and yields each non-empty batch. It follows Google’s next-page token until there are no more pages.

**Call relations**: The main paginate method calls this during the first file sync. The child-record reader also calls it so it can visit each file before asking for that file’s permissions, comments, or revisions.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 148–153)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This function asks Google Drive for a marker that represents the current end of the change log. The connector saves that marker so the next run can ask only for changes after this point.

**Data flow**: It receives an HTTP client, calls Google’s start-page-token endpoint, and reads the returned token. If the token is a real non-empty string, it returns it; otherwise it returns nothing.

**Call relations**: After the first full file listing, paginate calls this to create the future starting point for change-based syncing. That token is then yielded as the next cursor for the broader sync system to store.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 155–200)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This function reads Google Drive’s change feed after a previous sync. It lets the system update only what changed instead of re-reading every file.

**Data flow**: It starts with a saved change token, asks Google for change pages, and separates the results into updated file records and deleted file IDs. Removed files, or files now in trash, become delete markers. Normal changed files become records to upsert. Each yielded page includes records, deletes, and the next cursor. If Google says the token has expired, it raises a cursor-expired signal so the system can do a fresh full sync.

**Call relations**: The main paginate method calls this whenever the files stream already has a cursor. It creates StreamPage objects so the sync engine can both save changed records and remove records that disappeared from Drive.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 202–219)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists the shared drives the account can see. Shared drives are read as a full list each run rather than through a change feed.

**Data flow**: It receives an HTTP client, asks Google for shared drives in pages, converts each response’s drive list into a safe list, and yields each non-empty batch. It keeps following next-page tokens until Google has no more pages.

**Call relations**: The main paginate method calls this when the requested stream is shared drives. It supplies plain record batches back to the sync engine.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 221–259)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches per-file side information, such as permissions, comments, or revisions. It first walks through files, then asks Google for the chosen sub-list for each file.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It lists all current files, takes each valid file ID, and calls the matching Google endpoint under that file. It skips files where Google returns common child-access refusals, filters child records by cursor when the stream has a time field, and adds the parent file ID and file name to each returned child record before yielding it.

**Call relations**: The main paginate method calls this for permissions, comments, and revisions. This helper depends on _paginate_files to know which files to inspect, then yields enriched child records so the rest of the system can connect each permission, comment, or revision back to its file.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 261–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a Google Drive file record into readable text for recall or search display. For non-file streams, it lets the base connector use its normal rendering behavior.

**Data flow**: It receives one record and its stream description. If the stream is not files, it passes the work upward to the shared rendering logic. For file records, it pulls out the name, MIME type, owners, and web link, then returns a title and a small text body that a person can understand.

**Call relations**: The broader source framework calls this when it needs a human-readable version of a synced record. It uses the local _str helper to safely turn the file name into text.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 279–280)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only if it is already text. It prevents non-text values from being used as a file title by accident.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this while building the title for a file record. It keeps the render output predictable even when Google data is missing or oddly shaped.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googlemeet.py`

`io_transport` · `source sync and page rendering`

Google Meet stores useful meeting memory in several places: the meeting record itself, transcript sessions, individual transcript lines, and sometimes a Google Docs file containing AI notes. This connector brings those scattered pieces together into one plain record per meeting, like gathering papers from different folders and stapling them into one packet.

During a sync, it asks the Google Meet API for conference records, newest first. If the system already has a saved cursor, it looks back one extra day because transcripts and notes may appear after the meeting ends. For each conference, it fetches transcript artifacts and smart-note artifacts. A meeting is emitted only if it has at least one transcript or smart note, so empty meetings do not become useless pages.

The connector is careful about Google permissions. If the Meet API says the grant is not allowed to read these artifacts, the stream is marked as skipped rather than crashing the whole run. If a linked Google Doc for notes or transcripts is missing or forbidden, the connector keeps the link and continues instead of failing.

Finally, it renders the gathered data as readable text: meeting details, transcript dialogue grouped by speaker, and AI summaries. That rendered page is what the rest of the system can index and retrieve.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 55–89)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main fetch loop for the Google Meet stream. It asks Google for conference records page by page, builds complete meeting records for conferences that have transcripts or smart notes, and yields them to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It builds Google Meet API request parameters, optionally using a one-day lookback from the cursor, then reads conference pages. For each conference it asks for the detailed record, keeps only records with artifacts, calculates the next cursor from start times, and outputs StreamPage objects. If Google refuses because of missing access scope, it turns that into a skipped stream.

**Call relations**: The sync driver calls this when it wants records from the meeting_artifacts stream. Inside the loop it uses _lookback to avoid missing late-generated artifacts, _max_start_time to advance progress, and _conference_record to turn a raw conference into the full record that later rendering will use.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 3 external calls (__init__, list_or_empty, refused_for_scope).


##### `GoogleMeetConnector._conference_record`  (lines 91–114)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete in-memory version of one Google Meet conference. It combines the conference’s basic details with all transcript and smart-note artifacts found under it.

**Data flow**: It receives a raw conference object from the Meet API. It reads the conference resource name, fetches transcript artifacts and smart-note artifacts below that resource, turns each one into a cleaner dictionary, and returns one meeting record with identifiers, times, space information, transcripts, and smart notes.

**Call relations**: paginate calls this for every conference returned by Google. This function then fans out to _artifacts to list child items, _transcript to expand transcript details, and _smart_note to expand AI note details before handing the assembled record back to paginate.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 116–133)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This lists the artifacts attached to a conference, such as transcript sessions or smart notes. It hides the API paging details so callers get a simple list.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the collection name to read. If there is no parent, it returns an empty list. Otherwise it repeatedly calls the Google Meet API, follows page tokens, collects the returned artifact objects, and returns the full list.

**Call relations**: _conference_record uses this twice: once for transcripts and once for smartNotes. It supplies the raw artifact objects that _conference_record then passes to _transcript or _smart_note for richer processing.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 135–147)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google Meet into a record that is easier for the system to store and render. It includes both transcript metadata and the individual spoken entries.

**Data flow**: It receives a raw transcript artifact. It extracts the transcript name, ID, state, start and end times, and linked Google Docs destination, then calls _transcript_entries to fetch the actual lines of speech. It returns a dictionary containing all of that information.

**Call relations**: _conference_record calls this for each transcript artifact found by _artifacts. It delegates link extraction to _docs_destination and spoken-line fetching to _transcript_entries, then hands the completed transcript back to the conference record builder.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 149–182)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual lines inside a transcript, including speaker, text, language, and timing. It makes transcript sessions useful as readable dialogue rather than just a link to a transcript file.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is missing, it returns an empty list. Otherwise it reads entry pages from the Meet API, turns each raw entry into a small dictionary, and returns the list. If Google says the entries are missing or forbidden, it returns whatever it has collected so far instead of failing.

**Call relations**: _transcript calls this while building a transcript record. The dialogue formatter later uses these entries through _transcripts_section and _dialogue when the meeting page is rendered.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 184–199)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a record for storage and display. When possible, it also fetches the plain text from the linked Google Doc so the summary can be searched directly.

**Data flow**: It receives a raw smart-note object. It extracts the note’s ID, name, state, times, and Google Docs destination. If there is a linked document ID, it asks _document_text for the document’s plain text and adds it as the body when text is available. It returns the note record.

**Call relations**: _conference_record calls this for each smart-note artifact found by _artifacts. It uses _docs_destination for the document link and _document_text for optional inlined content, then returns the enriched note to the conference record.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 201–210)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a linked Google Docs document and extracts its plain text. It is used so AI meeting summaries can be included directly in the rendered page, not just linked.

**Data flow**: It receives an HTTP client and a Google Docs document ID. It safely quotes the ID for use in a URL, asks the Google Docs API for the document, and passes the returned structure to _plain_text. If the document is missing or access is forbidden, it returns an empty string; other HTTP errors are re-raised.

**Call relations**: _smart_note calls this when a smart-note artifact points to a Google Doc. This function hands the raw document structure to _plain_text, then gives the extracted text back to _smart_note for inclusion in the note record.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 212–229)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored meeting record into a readable text page. The page includes meeting metadata, transcript sections, and AI summaries in a format suitable for search and recall.

**Data flow**: It receives one record and its stream description. For the meeting_artifacts stream, it pulls out the title, conference details, transcript list, and smart-note list, formats each part, joins the non-empty pieces, and returns a title plus body text. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The broader source framework calls this after records have been fetched and need to become text. It uses _labeled for simple metadata blocks, _transcripts_section for transcript content, and _smart_notes_section for AI note content.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 232–248)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript records for one meeting into a readable section. It adds transcript metadata and converts transcript entries into speaker-labelled dialogue.

**Data flow**: It receives a value that should contain transcript records. It safely treats missing or invalid values as an empty list. For each transcript, it builds a small metadata block, asks _dialogue to format the spoken lines, and returns one joined text section headed “Transcripts”. If there are no transcripts, it returns an empty string.

**Call relations**: render calls this while building the final meeting page. It relies on _labeled for transcript details and _dialogue for the human-readable conversation text.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 251–267)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats Google Meet AI summaries into a readable section. It includes note metadata, a document link when present, and the inlined summary body if it was fetched from Google Docs.

**Data flow**: It receives a value that should contain smart-note records. It safely turns that into a list, then for each note builds metadata and adds the note body text if present. It returns a section headed “AI summaries”, or an empty string if there are no notes.

**Call relations**: render calls this as one part of the final meeting page. It uses _labeled and _str to keep the output clean and avoid showing missing values.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 270–283)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This converts transcript entries into a simple conversation transcript. It labels each line by speaker and merges back-to-back entries from the same speaker so the output reads naturally.

**Data flow**: It receives a value that should contain transcript entry records. It walks through the entries, skips blank text, derives a speaker name, and builds lines like “Participant: text”. If the same speaker continues, it appends the text to the previous line. It returns the joined dialogue as a string.

**Call relations**: _transcripts_section calls this when it needs the spoken part of a transcript. It uses _speaker to choose a readable speaker label and _str to safely handle missing text.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 286–299)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. It strips away document formatting and keeps only the text runs.

**Data flow**: It receives a Google Docs document record. It looks inside the body content, visits paragraph elements, collects each textRun content string, joins all chunks together, trims the result, and returns plain text.

**Call relations**: _document_text calls this after successfully downloading a Google Doc. The returned text may then be attached to a smart-note record and later shown by _smart_notes_section.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 302–309)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls the Google Docs destination information out of a Meet artifact. That gives the system both the document ID and the export link when Google provides them.

**Data flow**: It receives a transcript or smart-note record. If the record has a docsDestination dictionary, it extracts the document resource and export URL as safe strings and returns them under standard keys. If not, it returns an empty dictionary.

**Call relations**: _transcript and _smart_note both call this while building their artifact records. The returned fields are later used either to fetch document text or to show the document link in the rendered page.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 312–318)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This finds the latest meeting start time in a page of conference records. It helps the connector remember how far it has synced.

**Data flow**: It receives a list of conference records and the current cursor. It compares each valid startTime string with the current value and keeps the greatest one. It returns the updated cursor, or the original cursor if nothing newer is found.

**Call relations**: paginate calls this after reading a page of conferences. The result becomes the next cursor in the StreamPage so future syncs can resume from the right point.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 321–323)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor back by one day. It exists because Google may generate transcripts or notes after the meeting, so the connector intentionally rechecks a small recent window.

**Data flow**: It receives a timestamp string. It parses it as a date and time, subtracts the configured one-day lookback, formats the result back into the timestamp style Google expects, and returns that string.

**Call relations**: paginate calls this when it has a cursor and needs to build the Google Meet API filter. The adjusted timestamp makes the next API request include recently synced meetings again.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 326–327)

```
def _resource_id(name: str) -> str
```

**Purpose**: This gets the short ID from a Google resource name. Google often returns names like paths, and this helper keeps only the final piece after the last slash.

**Data flow**: It receives a resource name string. If the string is present, it splits from the right at the last slash and returns the final segment. If the name is empty, it returns an empty string.

**Call relations**: Several record-building helpers call this to create stable IDs for conferences, transcripts, entries, and smart notes. _speaker also uses it to turn participant resource names into shorter speaker labels.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 330–332)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses the label used for a transcript speaker. It turns a participant reference into a short readable name, falling back to “Participant” when no usable name exists.

**Data flow**: It receives any participant value from a transcript entry. It first keeps it only if it is a string, then extracts the final resource ID. If that result is blank, it returns the generic label “Participant”.

**Call relations**: _dialogue calls this for each transcript entry while building speaker-labelled conversation lines. It uses _str and _resource_id to make the label safe and compact.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 335–336)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only when it is actually a string. It prevents unexpected data shapes from leaking into rendered text or identifiers.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Many functions call this whenever they read optional fields from Google API responses. It keeps record building and rendering predictable even when Google omits a field or returns a non-string value.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 339–340)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats simple metadata as labelled lines, such as “start: 2024-...” or “doc: https://...”. It skips empty values so the rendered page stays clean.

**Data flow**: It receives a list of label-and-value pairs. For each pair with a non-empty value, it creates one “label: value” line, joins the lines with newlines, and returns the resulting text block.

**Call relations**: render uses this for conference details, while _transcripts_section and _smart_notes_section use it for artifact details. It provides the common small building block for readable metadata throughout the final page.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/providers/googlesheets.py`

`io_transport` · `source sync and record rendering`

This connector is the bridge between UFO and Google Sheets. Without it, the system could not turn a user’s spreadsheets into recallable records such as “this spreadsheet exists,” “these are its tabs,” and “these are the rows in this tab.”

The connector starts with Google Drive because Drive is the place that can list spreadsheet files and tell when each file was last modified. It then fans out to the Google Sheets API to read each spreadsheet’s tab list and, for the values stream, the rows inside each tab. It works incrementally: it stores a cursor, meaning a bookmark, based on Drive’s modified time so later runs can ask Google only for files changed since that point.

A lot of the file is about being careful when access is partial. If a whole grant lacks the right permission, the stream is skipped rather than pretending there is no data. If only one file or one tab is refused, the connector records that refusal and keeps moving so one bad spreadsheet does not block the entire sync. Later runs can retry those refused file IDs.

It also prepares records for display. Spreadsheet records summarize tabs, sheet records point back to their spreadsheet, and value records render rows as simple text separated by vertical bars.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 153–203)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Google Sheets. It produces pages of records for one stream, while remembering the latest safe cursor and any files that need to be retried later.

**Data flow**: It receives an HTTP client, a stream choice such as spreadsheets, sheets, or sheet_values, and an optional stored cursor. It decodes that cursor into a modified-time watermark and a list of previously refused files, lists changed spreadsheets, turns each visited file into records, and yields StreamPage objects. If it reaches files that were refused before, it retries them after the normal listing and updates the cursor to say which refusals remain.

**Call relations**: The runtime calls this when it wants records from the connector. It relies on _decode_cursor at the start, _spreadsheet_visits to walk the Drive listing, _visit_records to create the requested stream’s records, _carried_visit for retrying old refusals, _settled to update the refusal set, and _encode_cursor before yielding pages. If Google reports that the whole grant cannot read Drive or Sheets, it raises StreamSkipped so the run records a skipped stream instead of failing mysteriously.

*Call graph*: calls 7 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _settled); 2 external calls (__init__, refused_for_scope).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 205–230)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks Google Drive for batches of spreadsheet files. It is the low-level Drive listing step that finds candidate spreadsheets before any Sheets-specific data is read.

**Data flow**: It receives an HTTP client and an optional watermark time. It builds a Drive search query for untrashed Google Sheets, adds a modified-time filter when a watermark exists, follows Drive page tokens, and yields lists of file metadata. The output is one batch at a time, with empty or missing file lists normalized to an empty list.

**Call relations**: _spreadsheet_visits calls this to get the Drive-side list of spreadsheets. It does not build final records itself; it only supplies the raw file metadata that later steps enrich with Sheets API details.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 232–240)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This function turns Drive-listed spreadsheet files into visit objects that contain the best available spreadsheet metadata. It skips malformed Drive entries that do not have a usable file ID.

**Data flow**: It receives an HTTP client and a watermark. It loops through batches from _iter_spreadsheet_files, pulls out each spreadsheet ID, and asks _file_visit to combine Drive metadata with Sheets metadata. It yields _FileVisit objects, each saying which file was visited, what record was built, and whether access was refused.

**Call relations**: paginate uses this during the normal listing part of a sync. This function sits between Drive enumeration and record creation: it gets file candidates from _iter_spreadsheet_files and hands each one to _file_visit for enrichment.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 242–256)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This function retries a file ID that was refused in an earlier run. It checks whether the file is now readable, deleted, trashed, or still refused.

**Data flow**: It receives an HTTP client and a spreadsheet file ID. It asks Drive for that one file’s metadata, including whether it is trashed. If Google says the file itself is refused or gone, it returns a _FileVisit with no record and a refusal flag as appropriate. If the file is trashed, it clears the refusal. Otherwise it passes the metadata to _file_visit so the file can be read normally.

**Call relations**: paginate calls this after finishing the normal Drive listing, but only for carried refused IDs that were not already encountered in the current listing. It uses _is_per_file_refusal and google.error_detail to decide whether an error is about this one file rather than a wider permission problem, then may hand off to _file_visit.

*Call graph*: calls 2 internal fn (_file_visit, _is_per_file_refusal); called by 1 (paginate); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._file_visit`  (lines 258–285)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This function builds the main spreadsheet record for one Drive file. It tries to read full Sheets metadata, but falls back to Drive metadata if this specific spreadsheet is inaccessible.

**Data flow**: It receives an HTTP client, a file ID, and Drive metadata for that file. It asks the Sheets API for spreadsheet details such as title and tab list. If that one file is refused, it marks the visit as refused and creates a minimal metadata object from Drive’s file name. It returns a _FileVisit containing the merged record, timestamps from Drive, and whether a refusal happened.

**Call relations**: _spreadsheet_visits calls this for files found in the normal Drive list, and _carried_visit calls it for retried file IDs. It uses _is_per_file_refusal to distinguish a single-file access problem from errors that should stop or skip the stream.

*Call graph*: calls 1 internal fn (_is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._visit_records`  (lines 287–303)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This function converts one visited spreadsheet into records for the stream currently being synced. It is the dispatcher that says, “for this file, what should this stream emit?”

**Data flow**: It receives an HTTP client, a stream description, and a _FileVisit. If the visit has no record, it returns no records and keeps the refusal flag. For the spreadsheets stream, it returns the spreadsheet record itself. For the sheets stream, it expands the spreadsheet into one record per tab. For the sheet_values stream, it reads the tab grids and returns one value record per tab.

**Call relations**: paginate calls this for every normal and carried visit. It delegates tab-record building to _sheet_records and row-value reading to _sheet_value_records, then returns both the records and whether any access refusal should remain in the cursor.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 305–361)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This function reads the actual cell rows from each tab in a spreadsheet. It batches requests for efficiency, but can fall back to one-tab-at-a-time reads to find tabs that are refused.

**Data flow**: It receives an HTTP client and a spreadsheet record. It extracts valid tab titles and IDs, groups them into chunks, and asks the Sheets API for each chunk’s row values. For successful batches, it turns each returned value range into a record. If a batch is refused for this file, it retries each tab separately, drops only tabs that are individually refused, and notes that a refusal happened. It returns the value records plus a flag saying whether any tab was refused.

**Call relations**: _visit_records calls this only for the sheet_values stream. It uses _quoted_sheet_range to name tabs safely, _sheet_value_record to build final records, _is_per_file_refusal and google.error_detail to classify access errors, and StreamFault if Google returns a batch response whose size does not match the request.

*Call graph*: calls 4 internal fn (__init__, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 3 external calls (list_or_empty, error_detail, quote).


##### `GoogleSheetsConnector.render`  (lines 363–382)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns stored Google Sheets records into readable text for recall or display. It chooses a useful title and body depending on whether the record is a spreadsheet, a tab, or tab values.

**Data flow**: It receives a record and its stream description. For spreadsheet records, it makes a body listing tab names. For sheet records, it names the parent spreadsheet. For value records, it converts rows into plain text. It returns a title and a markdown-like text block with a heading.

**Call relations**: The source framework calls this when it needs a human-readable version of a synced record. It uses _str to safely read optional strings and _grid_text to format tab rows; unknown stream names fall back to the parent connector’s render behavior.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 385–398)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This helper reads the stored sync bookmark for Google Sheets. It supports both old simple cursors and newer JSON cursors that also remember refused file IDs.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns no watermark and no carried refusals. If the cursor is plain text or not a valid checkpoint object, it treats that text as the watermark. If it is a valid checkpoint JSON object, it returns the watermark, refused file IDs, and the last retried ID.

**Call relations**: paginate calls this at the start of a sync to know where to resume. Its output controls which Drive files are listed and which refused files are retried later.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 401–406)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This helper writes the sync bookmark that will be stored after a page. It keeps the cursor simple when there are no refused files, and uses JSON only when extra retry information is needed.

**Data flow**: It receives a watermark time, a set of refused file IDs, and an optional last-retried ID. If there is no watermark or no refused files, it returns just the watermark. Otherwise it builds a checkpoint object with a sorted, capped list of refused IDs and serializes it as JSON.

**Call relations**: paginate calls this whenever it yields a page or final checkpoint. The result is what lets later runs resume from the right modified time and retry files that were previously inaccessible.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 409–410)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This helper updates the set of refused file IDs after one file has been tried. It answers the simple question: should this file stay on the retry list or be removed?

**Data flow**: It receives the current refusal set, a file ID, and a yes-or-no value saying whether that file is still refused. If still refused, it returns a set with the file included. If not, it returns a set with the file removed.

**Call relations**: paginate calls this after each visited or retried file. Its result is passed into _encode_cursor so the stored cursor reflects the latest access state.

*Call graph*: called by 1 (paginate).


##### `_is_per_file_refusal`  (lines 413–419)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This helper decides whether an HTTP error means “this particular file or tab is inaccessible.” That matters because single-file refusals can be skipped and retried without stopping the whole stream.

**Data flow**: It receives an HTTP status code and parsed Google error details. It returns true only for 403 or 404 errors with a real Google error body, excluding quota problems and grant-wide permission problems. The output is a yes-or-no classification used by the caller.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this when Google rejects a Drive or Sheets request. It consults google.is_quota_refusal and _is_grant_refusal so quota failures and missing-scope failures are not mistaken for ordinary inaccessible files.

*Call graph*: calls 1 internal fn (_is_grant_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records); 1 external calls (is_quota_refusal).


##### `_is_grant_refusal`  (lines 422–427)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This helper detects errors that mean the user’s overall Google permission grant is not good enough. These are different from one spreadsheet being private or deleted.

**Data flow**: It receives parsed Google error details. It looks for known reasons such as missing configuration or insufficient permissions, and for Google service-domain error details. It returns true when the error points to the credential, project, service, or permission grant rather than one file.

**Call relations**: _is_per_file_refusal calls this while classifying 403 and 404 errors. If this returns true, the caller should not treat the problem as a retryable single-file refusal.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 430–451)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper expands one spreadsheet metadata record into one record per tab. It makes tabs addressable as separate pieces of content.

**Data flow**: It receives a spreadsheet record containing its ID, title, timestamps, and Sheets API tab metadata. It loops through valid sheet entries, ignores malformed ones, and creates a record with a stable ID made from spreadsheet ID and sheet ID. The output is a list of tab records that keep links back to the parent spreadsheet.

**Call relations**: _visit_records calls this for the sheets stream. It does not contact Google; it reshapes metadata that _file_visit already fetched.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 454–467)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This helper builds one final record for the row values of one tab. It attaches spreadsheet and sheet identity to the raw values returned by Google.

**Data flow**: It receives the parent spreadsheet record, the tab title, the tab ID, and a Google value-range response. It copies the value-range data and adds a stable ID, spreadsheet title and ID, sheet title and ID, and timestamps. The output is one record representing the tab’s grid values.

**Call relations**: _sheet_value_records calls this after either a successful batch value read or a successful one-tab retry. The produced records are returned up to _visit_records and then paginated by paginate.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 470–472)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This helper formats a tab title so Google Sheets will interpret it as a sheet name, not as a cell reference or named range. It is especially important for titles with spaces or apostrophes.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title, then wraps the result in apostrophes. The output is a safe A1-style sheet reference such as 'My Sheet'.

**Call relations**: _sheet_value_records calls this when building ranges for batch and individual value requests. Correct quoting ensures the connector reads the intended tab.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 475–480)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This helper turns a tab’s cell values into simple readable text. It makes rows look like lines and cells look like columns separated by vertical bars.

**Data flow**: It receives any value. If the value is not a list of rows, it returns an empty string. Otherwise it walks each row that is a list, converts each cell to text, joins cells with ` | `, and joins rows with newlines.

**Call relations**: render calls this for sheet_values records. It is the last formatting step before tab rows become recallable display text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 483–484)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely returns a value only if it is already a string. It prevents display code from accidentally showing non-text values as titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render calls this when preparing titles and short bodies for spreadsheet and sheet records. It keeps the rendered text clean when optional fields are missing or have unexpected types.

*Call graph*: called by 1 (render).


### Microsoft Graph Workspace
Microsoft Teams and Outlook connectors use Microsoft Graph to sync collaboration, mail, contacts, calendar, and conversation data.

### `extensions/sources/ufo_ext_sources/providers/microsoft_teams.py`

`io_transport` · `source sync`

Microsoft Teams keeps its data behind Microsoft Graph, Microsoft’s web API for Office 365 data. This file is a read-only connector for that API. Its job is to walk through the signed-in user’s Teams world: first the teams they have joined, then each team’s channels, then each channel’s messages; separately, it also reads the user’s chats and each chat’s messages.

The connector treats the Microsoft API like a paged notebook. Each request returns one page of results, and the shared REST connector follows the “next page” link until there are no more pages. For messages, it supports incremental syncing: if the system already knows the last message update time, this connector only yields messages modified after that point.

It also adds context to nested records. For example, a channel message is not useful by itself unless you know which team and channel it came from, so the connector attaches those IDs before passing the message onward.

A key detail is fault tolerance. If one team, channel, or chat cannot be read because it was removed or access is denied, the connector skips that parent and keeps syncing the rest. But if the whole grant cannot list teams or chats at all, it records the stream as skipped rather than treating the entire run as a hard crash.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Teams that the signed-in Microsoft account has joined. This is the starting point for reading team channels and channel messages.

**Data flow**: It receives an asynchronous HTTP client that can talk to Microsoft Graph. It asks the `/me/joinedTeams` endpoint for pages of teams, gathers all the returned team records into one list, and returns that list to the caller.

**Call relations**: When `paginate` is asked for the `teams` stream, it calls this directly and yields the result. `_channels` also calls it first because channels can only be fetched after the connector knows which teams exist.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches channels for every joined team and labels each channel with the team it belongs to. This preserves the parent-child relationship that Microsoft Graph returns through separate API calls.

**Data flow**: It starts by asking `_teams` for all joined teams. For each team with a valid ID, it requests that team’s channels from Microsoft Graph. Before yielding each page of channels, it adds extra context such as the team ID and team name. If one team’s channels cannot be read because access is denied or the team is missing, it skips that team and continues with the others.

**Call relations**: This function sits between the broad team list and the deeper message sync. `paginate` calls it when syncing the `channels` stream, and `_channel_messages` calls it so it knows which channels to inspect for messages. It uses `with_context` to attach the team information that later steps need.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from every channel the user can access. It supports incremental syncing so the system does not repeatedly process old channel messages.

**Data flow**: It receives the HTTP client and an optional cursor, which is the last saved update time from a previous sync. It asks `_channels` for channel pages, then requests messages for each valid team-and-channel pair. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is newer. For any non-empty batch, it adds the team ID, channel ID, and thread ID before yielding the messages. If one channel cannot be read because it is unavailable or forbidden, that channel is skipped.

**Call relations**: This is called by `paginate` when the system wants the `channel_messages` stream. It depends on `_channels` for the list of places to look, and it uses `with_context` so downstream storage can remember where each message came from.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Microsoft Teams chat conversations visible to the signed-in user. These are separate from team channels and need their own path through Microsoft Graph.

**Data flow**: It receives an asynchronous HTTP client, requests pages from `/me/chats`, collects all chat records into a list, and returns that list.

**Call relations**: When `paginate` is asked for the `chats` stream, it calls this directly. `_chat_messages` also calls it first because chat messages can only be fetched after the connector knows which chats exist.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from each Teams chat conversation. Like channel message syncing, it can skip old messages using the saved update cursor.

**Data flow**: It receives the HTTP client and an optional cursor. It asks `_chats` for all visible chats, then requests messages for each valid chat ID. If a cursor is provided, it filters out messages whose last modified time is not newer than that cursor. For each non-empty message batch, it adds the chat ID and thread ID before yielding it. If one chat cannot be read because it is missing or access is denied, it skips that chat and continues.

**Call relations**: This function is used by `paginate` for the `chat_messages` stream. It depends on `_chats` for the list of chat conversations and uses `with_context` to keep each message tied to its chat.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Routes each requested stream name to the right Microsoft Teams reading routine. It is the main doorway the shared source-sync runner uses to pull Teams data from this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it calls the matching helper for teams, channels, channel messages, chats, or chat messages, then yields pages of records back to the sync system. If Microsoft Graph refuses access to a whole top-level stream because the grant lacks permission, it raises `StreamSkipped` so the run records a controlled skip. If the stream name is unknown, it also raises `StreamSkipped` to say this connector does not implement it.

**Call relations**: The source framework calls this during sync whenever it wants records for one stream. `paginate` then fans out to `_teams`, `_channels`, `_channel_messages`, `_chats`, or `_chat_messages`. It is also where broad permission failures are translated into a skip instead of an unhandled failure.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a Microsoft Teams record into readable text for storage or recall. Messages get special treatment because Microsoft Graph stores their body as HTML rather than plain text.

**Data flow**: It receives one record and the stream it came from. For non-message streams, it falls back to the normal REST connector rendering. For channel and chat messages, it reads the subject, extracts `body.content`, strips HTML tags from the body, and returns a title plus a simple text document with a heading and message body.

**Call relations**: The sync system calls this when it needs a human-readable version of a record. For message streams, it uses `_str` to safely read the subject, `get_path` to reach the nested body content, and `_strip_html` to turn the HTML body into plain text.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a small piece of HTML into rough plain text by removing tags. This makes Teams message bodies easier to read outside the Microsoft client.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces HTML tags with spaces, trims extra space from the ends, and returns the cleaned text.

**Call relations**: Only `MicrosoftTeamsConnector.render` calls this helper, and only for channel and chat messages. It is the last cleanup step before the message body is placed into the text document returned by `render`.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. This avoids putting unexpected non-text data into message titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: Only `MicrosoftTeamsConnector.render` calls this helper. It is used when building the message title from the Teams message subject.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/outlook.py`

`io_transport` · `active during Outlook source sync runs`

Outlook data lives behind Microsoft Graph, Microsoft’s web API for mailbox and calendar information. This file is the project’s Outlook reader. Without it, the system would not know how to ask Graph for mailbox data, how to follow Graph’s paging links, or how to remember the special “delta” links that say “next time, continue from here.”

The main class, OutlookConnector, defines several streams: contacts, messages, conversations, events, and mail folders. A stream is one kind of data the sync engine can pull. For most streams, Graph returns data in pages and eventually gives a delta link. That link is like a bookmark in a long book: save it, and next time you open exactly where changes begin.

Messages and contacts are trickier because they can live in folders. This connector keeps a separate saved bookmark for each folder, packed into JSON text. Conversations are not directly synced as their own Graph object here. Instead, the connector reads messages and groups them by conversationId to create one conversation record per email thread.

The file also turns raw Graph records into friendlier fields, such as contact email, message sender, event title, and plain-text event description. If Graph rejects access with a permission error, the stream is skipped cleanly rather than making the whole run look broken.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: Formats a Python date-and-time value into the exact UTC timestamp text Microsoft Graph expects in filters. This is used when the connector asks Graph for items after a certain time.

**Data flow**: A datetime goes in. The function converts it to UTC and writes it as text like 2024-01-01T12:00:00Z. That formatted string comes out and can be placed into a Graph query.

**Call relations**: Conversation and message syncing call this when they need a starting time for their first read. It gives them Graph-friendly time text before they build their filter.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Turns a small piece of HTML into rough plain text by removing tags. It is used so event descriptions are easier to read and search.

**Data flow**: Any value goes in. If it is not text, the function returns nothing. If it is text, HTML tags such as <p> or <br> are replaced with spaces, surrounding spaces are trimmed, and the cleaned text comes out.

**Call relations**: The flatten step calls this while preparing calendar events. It takes Graph’s HTML body content and produces a simpler description field for the rest of the system.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address in an Outlook contact record. Contacts can hold several addresses, so this chooses one simple primary-looking value.

**Data flow**: A contact dictionary goes in. The function looks at its emailAddresses list, reads each nested emailAddress.address value, and returns the first non-empty address it finds. If there is no usable address, it returns nothing.

**Call relations**: The flatten step calls this for contact records. It relies on the shared get_path helper to safely read a nested field without crashing if pieces are missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds a useful phone number in an Outlook contact record. It prefers the mobile phone number, then falls back to the first business phone.

**Data flow**: A contact dictionary goes in. The function checks mobilePhone first. If that is missing, it scans businessPhones for the first non-empty text value. It returns the chosen phone number or nothing.

**Call relations**: The flatten step calls this when turning a raw contact into a friendlier contact shape. It supplies the simple phone field used downstream.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the connector’s public paging entry for the sync runner. It accepts the runner’s standard inputs and passes the relevant ones into Outlook’s actual paging logic.

**Data flow**: The HTTP client, stream description, saved cursor, optional user id, and optional backfill date come in. The user id is not needed here. The function forwards the stream, cursor, and backfill date to paginate, and yields whatever pages paginate produces.

**Call relations**: The wider source framework calls this when it wants Outlook records. This method is a small adapter that hands the work to OutlookConnector.paginate.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right syncing method for the requested Outlook stream. It is the traffic director that sends contacts, messages, conversations, events, and folders down different paths.

**Data flow**: A stream name, saved cursor, and optional backfill date come in. The function checks which stream is being requested, then yields pages from the matching helper. If Microsoft Graph says access is forbidden or unauthorized, it turns that into a clean StreamSkipped result; if the stream is unknown, it also reports it as skipped.

**Call relations**: paginate_source calls this during a sync. From here, the flow branches to _conversation_pages, _message_delta_pages, _contact_delta_pages, _event_delta_pages, or _graph_delta_pages depending on the stream.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records by reading messages and grouping them into email threads. Microsoft Graph messages contain a conversationId, and this function collapses many messages into one conversation summary.

**Data flow**: An HTTP client, a saved cursor, and an optional backfill date go in. The function asks Graph for messages ordered by lastModifiedDateTime, optionally filtered to only newer messages. It groups messages by conversationId, keeps the earliest creation time and newest update time, and yields one list of conversation records.

**Call relations**: paginate calls this when the conversations stream is requested. It uses _graph_instant when it needs to express the first backfill date in Graph’s timestamp format.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads one Microsoft Graph delta feed and turns it into the project’s standard page format. A delta feed is Graph’s way of returning both new or changed records and deletions since a saved bookmark.

**Data flow**: An HTTP client, an initial Graph path, an optional saved cursor, and optional query parameters go in. The function follows Graph’s nextLink pages, separates normal records from deleted item markers, and yields StreamPage objects containing records, delete ids, and the next cursor to save.

**Call relations**: paginate uses this directly for mail folders, and the message, contact, and event helpers use it for their own delta feeds. It creates StreamPage objects so the rest of the sync engine receives data in a consistent shape.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs Outlook messages across all mail folders while keeping a separate Graph bookmark for each folder. This matters because Graph message delta feeds are folder-specific here.

**Data flow**: An HTTP client, a JSON cursor map, and an optional backfill date go in. The function decodes the saved folder-to-cursor map, lists the current mail folders, reads each folder’s message delta feed, tags each returned message with its folder id, updates that folder’s cursor, and yields StreamPage objects with the refreshed cursor map encoded again.

**Call relations**: paginate calls this for the messages stream. It calls _list_mail_folders to discover folders, _graph_delta_pages to read each folder, _graph_instant to build the first backfill filter, and the cursor map helpers to unpack and repack progress.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs Outlook contacts from the default contacts area and any contact folders. Like messages, each folder can have its own saved delta bookmark.

**Data flow**: An HTTP client and an optional JSON cursor map go in. The function decodes previous folder cursors, builds a folder list starting with the default contacts folder, reads each folder’s contact delta feed, updates that folder’s saved cursor, and yields StreamPage objects with records, deletions, and the updated cursor map. If the default contacts delta endpoint is missing or unsupported, it skips that one case and continues.

**Call relations**: paginate calls this for the contacts stream. It calls _list_contact_folders to find extra folders, _graph_delta_pages to read Graph changes, and _decode_cursor_map and _encode_cursor_map to maintain per-folder progress.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs calendar events through Graph’s calendar delta feed. It limits the first calendar view to a wide but finite time range around the present.

**Data flow**: An HTTP client and optional saved cursor go in. The function calculates a window from one year in the past to two years in the future, then asks _graph_delta_pages to read /me/calendarView/delta with that window unless a saved cursor already resumes the feed. StreamPage results come out unchanged.

**Call relations**: paginate calls this for the events stream. It hands the actual paging and deletion detection to _graph_delta_pages.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Fetches the ids of the user’s Outlook mail folders. Message syncing needs these ids so it can read each folder’s message delta feed.

**Data flow**: An HTTP client goes in. The function reads Graph’s /me/mailFolders pages, collects every non-empty folder id, and returns a list of ids.

**Call relations**: _message_delta_pages calls this before syncing messages. The returned folder ids decide which folder-specific delta feeds will be read.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Fetches the ids of the user’s Outlook contact folders. Contact syncing uses this to include contacts stored outside the default folder.

**Data flow**: An HTTP client goes in. The function reads Graph’s /me/contactFolders pages, collects every non-empty folder id, and returns a list of ids.

**Call relations**: _contact_delta_pages calls this before syncing contacts. The returned folder ids are added after the default contacts area and then read one by one.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into records with simpler, more consistent fields. This makes downstream search, storage, and display code less dependent on Graph’s nested shape.

**Data flow**: A raw record and its stream description go in. For contacts, the function adds name, email, phone, and created_at fields. For messages, it adds subject, snippet, sender address, sent time, and thread ids. For events, it adds title, plain-text description, start and end times, and location. Other streams pass through unchanged.

**Call relations**: The source framework calls this after records are read. It uses _first_email, _phone, _strip_html, and get_path to safely pull useful values out of Graph’s raw nested data.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Reads the saved per-folder cursor map from JSON text. This lets message and contact syncs remember a different Graph bookmark for each folder.

**Data flow**: A raw cursor string goes in. If it is missing, invalid JSON, or not a dictionary, the function returns an empty map. Otherwise, it returns a dictionary of folder ids to non-empty cursor strings.

**Call relations**: _message_delta_pages and _contact_delta_pages call this at the start of their work. It turns the stored cursor text into a form they can update folder by folder.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Writes the per-folder cursor map back into JSON text so it can be saved between sync runs. This is the companion to _decode_cursor_map.

**Data flow**: A dictionary of folder ids to cursor strings goes in. If it has entries, the function serializes it as sorted JSON text. If it is empty, it returns nothing.

**Call relations**: _message_delta_pages and _contact_delta_pages call this after updating folder cursors. The encoded result becomes the next cursor handed back in each StreamPage.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack Workspace
The Slack connector syncs users, channels, messages, threads, and authors into stable searchable records.

### `extensions/sources/ufo_ext_sources/providers/slack.py`

`io_transport` · `source sync runs`

Slack does not hand over an entire workspace in one simple download. It gives pages of results, uses cursors to say “there is more,” and sometimes reports errors inside a successful-looking HTTP response. This file wraps those Slack quirks so the rest of the system can ask for clean streams of records.

The connector exposes five streams: users, conversations, conversation threads, messages, and message participants. Users and conversations are treated like full snapshots: each sync lists everything Slack currently allows the token to see, so missing records can be marked as gone. Messages are different. They are read channel by channel from Slack history, newest first, using a per-channel walk so a busy channel does not cause a quiet one to be skipped. Think of it like reading several notebooks from the back: each notebook keeps its own bookmark.

The file also normalizes Slack’s raw shapes. It flattens user profiles, labels channel types, converts Slack timestamps into readable time strings, creates message IDs from channel and timestamp, builds thread records from replies, and creates participant records for senders. It deliberately skips messages sent by this app’s own live bot user, and it treats missing Slack permissions as skipped streams or skipped channels rather than crashing the whole sync.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a clear Python error for Slack failures that arrive as normal-looking responses. Slack often says HTTP success but includes `ok=false`, so this error keeps the actual Slack error code and any missing permission scope.

**Data flow**: It receives Slack’s error text and, optionally, the permission Slack says is needed. It builds a readable error message, stores the error code and needed scope on the exception, and returns a raised-ready error object.

**Call relations**: _ok_or_raise uses this when Slack’s response says the request failed. Later code can inspect the stored error code to decide whether to skip a stream, skip one channel, or treat the problem as a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard entry point the wider source-sync framework uses to ask this Slack connector for pages of records. It mainly forwards the request to the connector’s real pagination logic.

**Data flow**: It receives an HTTP client, a stream description, the saved cursor, the connector’s own Slack user ID, and an optional backfill cutoff time. It passes those values unchanged into `SlackConnector.paginate`, which produces pages of Slack-derived records.

**Call relations**: The source framework calls this method when it wants to read a Slack stream. This method hands control to `SlackConnector.paginate` so all stream-specific decisions stay in one place.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses how to read each Slack stream. It knows that users and conversations are simple paged lists, while messages must be read by walking each channel’s history separately.

**Data flow**: It receives the stream being synced, the saved cursor, the HTTP client, the current bot user ID, and an optional oldest allowed time. For users or conversations, it yields pages from the matching listing method. For message-related streams, it first builds a user lookup and a channel list, then asks `PartitionWalk` to move through each channel’s history and yield stream pages. If the stream name is unknown, it marks the stream as skipped.

**Call relations**: `paginate_source` calls this as the main dispatcher. It calls `iter_users`, `iter_conversations`, `user_index`, `_slack_ts`, and builds a `PartitionWalk`; inside that walk, the local `channel_pages` function delegates each channel to `_channel_pages`.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies the list of Slack channel IDs that should be walked for message history. It gives the channel walker one partition, meaning one independent channel bookmark, at a time.

**Data flow**: It reads the channel IDs already collected by `SlackConnector.paginate`. It yields each channel ID in order, with no transformation.

**Call relations**: `SlackConnector.paginate` passes this small generator into `PartitionWalk`. `PartitionWalk` uses it to know which channels should be read independently.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the generic partition walker to Slack-specific channel history reading. Given one channel and its current time bounds, it returns the pages for that channel.

**Data flow**: It receives a channel ID and a `PartitionBound`, which is the walker’s instruction for what time slice to read. It looks up the channel details and passes them, along with users and bot identity, into `_channel_pages`, which produces Slack history pages.

**Call relations**: `PartitionWalk` calls this while walking channel partitions. It hands the actual Slack API work off to `SlackConnector._channel_pages`.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Slack workspace users page by page. It turns Slack’s raw member objects into the simpler user records this system stores.

**Data flow**: It starts without a cursor, asks Slack’s `users.list` endpoint for a page, flattens valid member records with `_flatten_user`, yields a page if there are users, then uses `_next_cursor` to continue until Slack says there are no more pages.

**Call relations**: `SlackConnector.paginate` calls this for the users stream, and `SlackConnector.user_index` calls it when message processing needs user details. It relies on `_enumerate` for permission-aware API calls and `_next_cursor` for Slack pagination.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Slack channels and direct-message conversations page by page. It reshapes Slack’s channel objects into consistent conversation records with names, types, privacy flags, and timestamps.

**Data flow**: It asks Slack’s `conversations.list` endpoint for public channels, private channels, group messages, and direct messages. For each valid channel object, it picks useful fields, derives the conversation type, extracts nested topic and purpose text, converts creation time to an ISO time string, yields the page, and follows Slack’s next cursor until complete.

**Call relations**: `SlackConnector.paginate` calls this both for the conversations stream and before reading message history, because message history must know which channels exist. It uses `_enumerate`, `_conversation_type`, `_nested_value`, `_unix_to_iso`, and `_next_cursor`.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table of Slack users by user ID. Message processing uses this so it can attach names and email addresses to message senders.

**Data flow**: It reads all pages from `iter_users`. For each user record with a string ID, it stores that user in a dictionary keyed by ID, then returns the finished dictionary.

**Call relations**: `SlackConnector.paginate` calls this before syncing message-related streams. It depends on `iter_users`, so the same user normalization is reused instead of duplicated.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one Slack channel’s message history for the time window requested by the partition walker. It also knows when one unreadable channel should be skipped without stopping all other channels.

**Data flow**: It receives a channel, a stream description, time bounds, the user lookup, and the connector’s own Slack user ID. It builds Slack `conversations.history` parameters, including cursor and time limits, posts the request, filters valid raw messages, turns each page into a `WalkPage` through `_message_page`, and follows Slack’s next cursor until done. If Slack refuses this one channel for known reasons, it raises a partition skip instead of failing the whole stream.

**Call relations**: The local `channel_pages` function inside `SlackConnector.paginate` calls this for each channel chosen by `PartitionWalk`. It calls `_slack_post`, `_message_page`, and `_next_cursor`, and it signals per-channel refusal through `PartitionSkipped`.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific record type currently being synced: threads, messages, or message participants. It also reports the newest and oldest Slack timestamps in the page so the channel walker can keep its bookmark accurate.

**Data flow**: It receives the stream type, channel details, raw Slack messages, the user lookup, and the bot user ID. It skips Slack deletion notices after recording deleted message IDs, flattens normal messages, derives possible thread records and sender participant records, then returns a `WalkPage` containing only the records for the requested stream plus the page’s timestamp range.

**Call relations**: `_channel_pages` calls this after each Slack history API response. It uses `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message`, then wraps the result in a `WalkPage` for `PartitionWalk` to advance.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs a top-level Slack listing request, such as users or conversations, with special treatment for missing permissions. If Slack says the token cannot list the stream, the sync records a skip instead of a crash.

**Data flow**: It receives an HTTP client, a Slack API path, and query parameters. It calls `_slack_get`; if Slack reports a known permission refusal, or HTTP returns a known refusal status, it raises `StreamSkipped`. Otherwise it returns the decoded Slack data or lets unexpected errors rise.

**Call relations**: `iter_users` and `iter_conversations` call this for their list endpoints. It calls `_slack_get` and translates permission problems into the source framework’s skip signal.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Makes a Slack GET request and checks Slack’s own success flag. This hides the detail that Slack can report application-level failure inside a normal HTTP response.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It performs the inherited HTTP GET, passes the returned data to `_ok_or_raise`, and returns only data that Slack marked as successful.

**Call relations**: `_enumerate` calls this for Slack listing endpoints. It delegates Slack-specific success checking to `_ok_or_raise`.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Makes a Slack POST request and checks Slack’s own success flag. It is used for Slack history reads, which this connector performs with POST parameters.

**Data flow**: It receives an HTTP client, an API path, and an optional JSON body. It performs the inherited HTTP POST, sends the response data through `_ok_or_raise`, and returns successful Slack data.

**Call relations**: `_channel_pages` calls this for `conversations.history`. It relies on `_ok_or_raise` to turn Slack’s `ok=false` replies into usable exceptions.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether Slack’s response body says the request really succeeded. This is needed because Slack may use HTTP 200 while still saying `ok=false` in the JSON body.

**Data flow**: It receives decoded Slack response data. If `ok` is false, it extracts the Slack error code and optional needed scope, then raises `SlackApiError`; otherwise it returns the data unchanged.

**Call relations**: `_slack_get` and `_slack_post` both call this immediately after network requests. When it raises `SlackApiError`, higher-level code decides whether that error means skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s “next page” token in a response. Slack uses this cursor token to continue long lists.

**Data flow**: It receives decoded Slack response data. It looks inside `response_metadata.next_cursor`; if a non-empty string is present, it returns it, otherwise it returns `None` to mean there are no more pages.

**Call relations**: `iter_users`, `iter_conversations`, and `_channel_pages` call this after each Slack page. Its answer controls whether those loops keep asking Slack for more data.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack-style Unix seconds into a standard readable timestamp string. It safely returns nothing when the input is missing or not a real number.

**Data flow**: It receives any value. If the value can be treated as seconds since 1970, it converts it to an ISO-formatted UTC datetime string; booleans, missing values, and invalid values become `None`.

**Call relations**: `iter_conversations` uses this for channel creation times, and `_flatten_user` uses it for user update times. It calls Python’s datetime conversion routine.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a Python datetime into Slack’s timestamp string format for history bounds. It pads the value so string comparisons sort correctly against real Slack timestamps.

**Data flow**: It receives an optional datetime. If there is no datetime, it returns `None`; if the time is before the Unix epoch, it also returns `None`; otherwise it returns a fixed-width seconds-with-microseconds string.

**Call relations**: `SlackConnector.paginate` uses this to turn a backfill cutoff time into the floor passed to `PartitionWalk`. That floor limits how far back channel history should be read.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts a Slack message timestamp string into a standard UTC time string. This makes message and thread times easier for the rest of the system to store and compare.

**Data flow**: It receives a Slack timestamp string or nothing. Empty input returns `None`; a valid numeric timestamp becomes an ISO-formatted UTC datetime string; invalid text returns `None`.

**Call relations**: _flatten_message uses this for message sent times, and `_conversation_thread_from_message` uses it for thread update and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns one raw Slack user object into the project’s simpler user record. It pulls useful profile fields into predictable top-level names.

**Data flow**: It receives a raw Slack member dictionary. It reads profile information if present, normalizes email to lowercase, chooses the best display and real names, converts update time, and returns a dictionary with stable user fields.

**Call relations**: `iter_users` calls this for every valid Slack member. It uses `_first_text` to choose the first usable name and `_unix_to_iso` to format update timestamps.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into a normalized message record. It also filters out messages sent by this app’s own Slack bot user so the system does not index its own live output.

**Data flow**: It receives a raw message, channel details, user lookup, and optional self user ID. It checks that the message and channel have usable IDs, skips self-authored messages, finds sender details, chooses a thread ID, builds a stable message ID, formats the sent time, creates a short snippet, and returns the message record. If required identifiers are missing, it returns `None`.

**Call relations**: `_message_page` calls this for each raw Slack message. It uses `_slack_ts_to_iso`, `_snippet`, and `_first_text`; its output is then used to derive thread and participant records.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread record when a Slack message is either the root of a thread with replies or a reply inside a thread. Plain one-off messages do not become thread records.

**Data flow**: It receives a normalized message plus the original raw Slack message and channel details. It checks thread timestamps and reply counts, decides whether the message belongs to a real thread, then returns a thread summary with title, counts, privacy flags, creation time, and last update time. If there is no real thread, it returns `None`.

**Call relations**: `_message_page` calls this after flattening each message. It uses `_slack_ts_to_iso` to turn Slack thread timestamps into normal time strings.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system answer questions like who took part in a message or thread.

**Data flow**: It receives a normalized message and the user lookup. It chooses a sender handle, preferring email when available and otherwise using the Slack user ID. If no handle can be found, it returns `None`; otherwise it returns a participant record tied to the message, channel, and thread.

**Call relations**: `_message_page` calls this for each flattened message when building the message participants stream. It uses `_first_text` to choose the first usable sender handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Labels a Slack conversation as a direct message, multi-person direct message, private channel, or public channel. This gives downstream records a simple category instead of many Slack boolean flags.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s flags in priority order and returns one type string: `im`, `mpim`, `private_channel`, or `public_channel`.

**Call relations**: `iter_conversations` calls this while reshaping Slack channel records. Its result becomes the `conversation_type` field used later by messages and threads.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value from inside nested dictionaries. It prevents crashes when Slack omits a nested object such as a topic or purpose.

**Data flow**: It receives a starting dictionary and a path of keys. It walks down the path one key at a time; if any level is not a dictionary, it returns `None`; otherwise it returns the final value.

**Call relations**: `iter_conversations` calls this to pull `topic.value` and `purpose.value` from Slack channel objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from several candidates. It is a small helper for picking the best available name, email, or handle.

**Data flow**: It receives any number of values. It scans them in order, returns the first string that still has text after trimming whitespace, and returns `None` if none qualify.

**Call relations**: _flatten_user uses this for names, `_flatten_message` uses it for sender handles, and `_participant_for_message` uses it for participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short preview of message text. This gives search or listing views a compact summary without storing messy whitespace in the preview field.

**Data flow**: It receives optional message text. If there is no text, it returns `None`; otherwise it collapses repeated whitespace into single spaces and cuts the result to the configured snippet length.

**Call relations**: _flatten_message calls this when building the normalized message record. The snippet can later be reused by thread summaries through `_conversation_thread_from_message`.

*Call graph*: called by 1 (_flatten_message).

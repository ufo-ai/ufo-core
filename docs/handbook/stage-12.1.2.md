# Google Workspace source connectors  `stage-12.1.2`

This stage is a group of Google Workspace connectors. It is shared behind-the-scenes support for the system’s syncing work: it reaches into Google services, reads what the connected account is allowed to see, and turns that data into standard records the rest of the system can search and recall.

Each file covers one Google product. The Gmail connector reads mailboxes, converts messages into clear text, and keeps track of new and deleted emails so it does not start from scratch every time. The Calendar connector reads the primary calendar, saves events, and also records each attendee separately so the system can understand who was invited. The Docs connector finds documents through Drive, fetches their contents, and flattens Google’s nested document structure into plain text. The Drive connector reads files, shared drives, permissions, comments, and revision history, packaging Google’s page-by-page API results into the project’s normal stream format. The Sheets connector reads spreadsheets, tabs, and cell values. The Meet connector turns meeting transcripts and generated notes into searchable pages. Together, they make Google work data available to the larger recall system.

## Files in this stage

### Communication and scheduling
Connectors that turn mailbox and calendar activity into searchable records of messages, events, and attendees.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync and rendering`

Gmail does not store an email as one simple text field. A message is a nested MIME tree, which means its readable parts may be buried inside plain-text or HTML sections, encoded in a web-safe form of base64. Without this file, the system would mostly see hard-to-read raw Gmail data instead of something a person would recognize as an email.

The GmailConnector is the main piece. On the first run, it lists message IDs inside a fixed backfill window, usually recent mail, and records Gmail's history marker so the next run can ask only what changed. On later runs, it uses that marker to ask Gmail for added and deleted messages. Deleted messages become tombstones, which tell the rest of the system that a previously synced item is gone.

When message bodies are fetched, the file flattens Gmail's nested shape into a practical record: sender, recipients, subject, labels, plain body, HTML body, and direction. Rendering then turns that record into readable prose with From, To, Cc, Subject, and body text. If only HTML exists, a small HTML parser strips tags and keeps the visible words. The connector also treats missing permissions as a skipped stream, and an expired Gmail history marker as a signal to start over safely.

#### Function details

##### `GmailConnector.paginate_source`  (lines 92–102)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector entry point the wider source-sync system calls when it wants Gmail records. It passes along the saved cursor and the optional backfill cutoff, then delegates the real Gmail work to paginate.

**Data flow**: It receives an HTTP client, a stream description, the current cursor, the current user ID, and an optional date floor. It does not inspect the user ID here; it forwards the stream, cursor, and date floor into paginate. The output is an asynchronous stream of pages containing records, deletes, and cursor updates.

**Call relations**: The sync framework calls this method as the public seam for reading Gmail. It immediately hands control to GmailConnector.paginate, which decides whether this is a first-time backfill or an incremental history sync.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 104–141)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function decides how to read Gmail for one sync pass. If there is no cursor, it does an initial listing; if there is a cursor, it asks Gmail what changed since then.

**Data flow**: It receives the Gmail stream, an HTTP client, a cursor, and possibly a backfill date. It turns that into added message IDs, deleted message IDs, and the next Gmail history marker. It then fetches message bodies in chunks and yields StreamPage objects, putting deletes and the next cursor on the final page.

**Call relations**: GmailConnector.paginate_source calls this during sync. It calls _backfill for first runs, _history for later runs, _fetch_bodies to turn IDs into full records, and StreamPage to hand results back to the core sync machinery. If Gmail refuses access with a permission-like status, it raises StreamSkipped so the run records a skip instead of a hard failure.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 143–168)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-read pass for a mailbox. It lists message IDs in the pinned backfill window and chooses the Gmail history marker that future runs should continue from.

**Data flow**: It receives an HTTP client and an optional cutoff date. It first reads the mailbox profile history ID as a safe floor, then repeatedly asks Gmail for pages of message IDs, adding an after:<timestamp> search filter when a cutoff is present. It returns the collected IDs and a seed cursor chosen by _seed_history_id.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor. It calls _profile_history_id before listing and _seed_history_id after listing, so even an empty backfill window can still leave backfill mode and move into normal change tracking.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 170–173)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail's current mailbox history marker from the user's profile. That marker is used as a safe starting point when a backfill finds no messages.

**Data flow**: It receives an HTTP client, requests the Gmail profile endpoint, and reads the historyId field if it is a string. It returns that string or None if Gmail did not provide it in the expected form.

**Call relations**: GmailConnector._backfill calls this before listing messages. Reading it before the list matters because it prevents messages delivered during an empty backfill scan from being accidentally skipped.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 175–199)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the cursor that the next sync run should use after an initial backfill. It prefers the newest listed message's history ID, but falls back to the mailbox profile marker if needed.

**Data flow**: It receives an HTTP client, the list of message IDs found during backfill, and a floor history ID. If there are message IDs, it fetches the first one in minimal form and tries to read its historyId. If that fails because the message disappeared, or no message was listed, it returns the floor instead.

**Call relations**: GmailConnector._backfill calls this after collecting message IDs. Its result is passed back to GmailConnector.paginate as the next cursor, which lets future runs use the Gmail history feed instead of repeating the same backfill forever.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 201–236)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail's change log from a previous history marker. It finds which messages were added and which were deleted since the last successful sync.

**Data flow**: It receives an HTTP client and a saved history ID. It asks Gmail history pages for messageAdded and messageDeleted records, extracts message IDs from those records, updates the latest history marker as it goes, and returns net-added IDs, deleted IDs, and the next marker. If Gmail says the old marker expired, it raises CursorExpired.

**Call relations**: GmailConnector.paginate calls this on incremental runs. It uses _message_ids to pull IDs out of Gmail history entries. When it raises CursorExpired, the larger sync system can discard the stale cursor and refetch from scratch.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 238–252)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns Gmail message IDs into full flattened message records. It also tolerates messages that disappear between the change list and the body fetch.

**Data flow**: It receives an HTTP client and a list of message IDs. For each ID, it requests the full Gmail message; if Gmail returns 404, that message is skipped because it is already gone. Each successful raw message is passed through _flatten_message, and the function returns the list of flat records.

**Call relations**: GmailConnector.paginate calls this after it has gathered added IDs from either _backfill or _history. It hands each raw Gmail response to _flatten_message so the rest of the system does not need to understand Gmail's nested payload format.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 254–274)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a flattened Gmail record into text that a person can read and the recall system can search usefully. It avoids dumping Gmail's raw nested JSON.

**Data flow**: It receives one record and its stream description. For the messages stream, it reads subject, sender, recipients, and body fields, formats them as email-like header lines, chooses a title, and returns the title plus rendered text. For other streams, it falls back to the parent connector's normal rendering.

**Call relations**: The broader source system calls render when it needs human-readable content for a synced record. This method relies on _str for safe subject reading, _format_contact and _format_recipients for headers, and _message_body for the best available body text.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 277–286)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This helper pulls message IDs out of Gmail history entries. Gmail wraps IDs inside small nested objects, so this function extracts only the usable strings.

**Data flow**: It receives any value that should be a list of history entries. It ignores entries that are not dictionaries, looks for each entry's message.id, and collects non-empty string IDs. It returns a clean list of IDs.

**Call relations**: GmailConnector._history calls this separately for added-message entries and deleted-message entries. The cleaned IDs become the raw material for deciding which bodies to fetch and which tombstones to report.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 289–316)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Gmail message into the simple record shape the sync system stores. It lifts the useful email facts out of Gmail's nested payload.

**Data flow**: It receives a raw message dictionary from Gmail. It reads selected headers, extracts plain and HTML bodies, parses the sender and recipients, copies labels, and decides whether the message is inbound or outbound based on the SENT label. It returns a flat dictionary with fields like id, subject, from_handle, to, body_text, and body_html.

**Call relations**: GmailConnector._fetch_bodies calls this for every successfully fetched message. It calls _extract_bodies for MIME body text, _parse_first_address for the sender, and _addresses for To and Cc recipients.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 319–333)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches Gmail's nested MIME payload for the first plain-text body and the first HTML body. MIME is the email format that stores a message as parts inside parts, like folders inside folders.

**Data flow**: It receives the message payload dictionary. It walks through the payload tree, decodes the first text/plain part and the first text/html part it finds, and stores them by MIME type. It returns a pair: plain text if found, and HTML text if found.

**Call relations**: _flatten_message calls this while turning a raw Gmail message into a flat record. Inside it, the nested walk function performs the actual tree traversal and calls _b64url_decode for encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 323–330)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This inner helper recursively visits each part of a Gmail MIME payload. It is the part that actually digs through the tree to find readable body sections.

**Data flow**: It receives one payload part at a time. If that part is text/plain or text/html and has encoded body data, it decodes the data and remembers it if that type has not been found yet. Then it repeats the same process for any child parts.

**Call relations**: _extract_bodies starts this walk at the top payload. Whenever it finds encoded body data, it calls _b64url_decode to turn Gmail's stored text into normal Unicode text.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 336–342)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail's body text encoding. Gmail stores body parts as URL-safe base64, a text-safe way to carry binary or special-character data.

**Data flow**: It receives an encoded string. It adds any missing padding characters required by base64, decodes the bytes using URL-safe base64 rules, and turns the result into UTF-8 text, replacing invalid characters if needed. If decoding fails, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this whenever it finds an encoded plain-text or HTML body. It uses Python's base64.urlsafe_b64decode to do the actual decoding.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 345–352)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses a sender-like email header and returns the first address in a simple form. It separates the email address from the display name, such as turning "Ada <ada@example.com>" into address and name parts.

**Data flow**: It receives a header string or None. If the header is missing or cannot be parsed, it returns two None values. Otherwise it uses the standard email parser, lowercases the address, and returns the address plus the display name if present.

**Call relations**: _flatten_message calls this for the From header. It relies on email.utils.getaddresses, which understands common email address formatting.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 355–362)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses a recipient header into a list of structured contacts. It is used for headers that may contain many people, such as To or Cc.

**Data flow**: It receives a header string or None. If missing, it returns an empty list. Otherwise it parses all addresses, drops entries without an email address, lowercases each address, and returns dictionaries with handle and display_name fields.

**Call relations**: _flatten_message calls this for To and Cc headers. It uses email.utils.getaddresses so quoted names and comma-separated addresses are interpreted correctly.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 365–370)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This formats one contact for display in rendered email text. It shows either just the email address or "Name <address>" when a display name exists.

**Data flow**: It receives a possible email address and possible display name. If the address is not a non-empty string, it returns an empty string. Otherwise it combines the display name and address when possible, or returns the address alone.

**Call relations**: GmailConnector.render calls this for the sender line. _format_recipients also calls it for each recipient before joining them into a readable To or Cc line.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 373–380)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient contact dictionaries into one readable line. It prepares the text used after To: or Cc: in the rendered email.

**Data flow**: It receives any value that should be a list of recipient objects. If it is not a list, it returns an empty string. For each dictionary item, it formats the contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this when building To and Cc header lines. It delegates each individual contact to _format_contact so sender and recipient formatting stay consistent.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 383–391)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body text from a flattened Gmail record. It prefers the real plain-text body, falls back to stripped HTML, and uses the Gmail snippet only as a last resort.

**Data flow**: It receives a flat message record. It first checks body_text and returns the trimmed text if present. If not, it checks body_html and converts it to visible text through _HtmlText.extract. If neither body exists, it returns the trimmed snippet or an empty string.

**Call relations**: GmailConnector.render calls this after building the header block. When HTML is the only body available, it hands that HTML to _HtmlText.extract so rendered messages still contain human-readable words instead of tags.

*Call graph*: called by 1 (render).


##### `_str`  (lines 394–395)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely reads a value as a string. It prevents non-string values from accidentally becoming titles or header text.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this when reading the subject. That keeps subject handling simple and avoids displaying unexpected data types.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 402–404)

```
def __init__(self) -> None
```

**Purpose**: This prepares the small HTML-to-text parser used when an email has no plain-text body. It creates storage for the text pieces found while parsing.

**Data flow**: It receives the new parser instance. It initializes the base HTMLParser with automatic character-reference conversion, then creates an empty list to collect visible text and line breaks. It changes the parser object so it is ready to receive HTML.

**Call relations**: _HtmlText.extract creates a parser instance, which invokes this initializer. The parser's later callbacks add data into the list prepared here.


##### `_HtmlText.extract`  (lines 407–412)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It removes tags and keeps useful line breaks around block-like elements, similar to copying the visible words from a web page.

**Data flow**: It receives raw HTML text. It creates a parser, feeds the HTML into it, joins the collected pieces, normalizes extra spaces within each line, removes empty lines, and returns clean text.

**Call relations**: _message_body uses this when no plain-text email body is available but an HTML body exists. During parsing, the HTMLParser machinery calls handle_data, handle_starttag, and handle_endtag on the parser.


##### `_HtmlText.handle_data`  (lines 414–415)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records visible text found inside the HTML body. It is called by the HTML parser whenever it sees character data between tags.

**Data flow**: It receives a piece of text from the parser. It appends that text to the parser's internal list of parts. It returns nothing, but changes the parser's collected output.

**Call relations**: _HtmlText.extract triggers this indirectly by feeding HTML into the parser. The collected data later becomes part of the final plain-text body.


##### `_HtmlText.handle_starttag`  (lines 417–419)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag represents a block boundary. That keeps paragraphs, list items, table cells, and headings from running together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block-style tags, it appends a newline marker to the parser's collected parts. It ignores attributes and non-block tags.

**Call relations**: _HtmlText.extract triggers this indirectly while parsing HTML. Its newline markers are later cleaned up and used to shape the final readable text.


##### `_HtmlText.handle_endtag`  (lines 421–423)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break after closing block-style HTML tags. It helps preserve the natural separation between visible sections of an email.

**Data flow**: It receives the closing tag name. If the tag is one of the known block-style tags, it appends a newline marker to the parser's collected parts. It returns nothing, but affects the final text layout.

**Call relations**: _HtmlText.extract triggers this indirectly while parsing HTML. Together with handle_starttag and handle_data, it lets HTML-only emails be rendered as readable plain text.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `source sync / incremental fetch`

This connector is the bridge between Google Calendar and the rest of the system. Without it, calendar events would stay inside Google and could not be synced, searched, or recalled by this project.

It reads from Google's events API for the user's primary calendar. On the first run, it looks back over a recent window of time and asks Google for events, including deleted ones. Google then returns a special sync token, which works like a bookmark. On later runs, the connector sends that bookmark back to Google so it only receives changes since the last sync. If Google says the bookmark is too old, the connector reports that the cursor expired so the wider sync system can start fresh.

The file produces two streams from the same calendar data. The first stream, `calendar_events`, stores one record per event, including title, time, location, organizer, description, and attendees. The second stream, `event_attendees`, turns each event into one row per invited person, like splitting a guest list into individual index cards.

It is careful about permissions. If the connected account does not allow calendar access, it marks the stream as skipped rather than treating the whole run as broken. It only reads data; it does not create or change calendar events.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads pages of events from Google Calendar and turns them into sync pages for the rest of the system. It supports both full startup syncing and later incremental syncing using Google's saved sync token.

**Data flow**: It receives an HTTP client, a stream choice, and an optional cursor bookmark. If there is a cursor, it asks Google for changes since that bookmark; otherwise it asks for recent events from the lookback window. For each Google response, it separates normal records from cancelled events, converts records into the shape the system expects, and yields `StreamPage` objects containing new or changed records, deleted record IDs, and finally the next cursor. If Google says the cursor is expired, it raises `CursorExpired`; if access is refused because the calendar permission is missing, it raises `StreamSkipped`.

**Call relations**: This is the main reading loop for the connector. The sync framework calls it when it wants data for either `calendar_events` or `event_attendees`. For event records it hands each raw Google event to `_flatten_event`; for attendee rows it hands events to `_flatten_attendees`. It packages the results into `StreamPage` objects so the core sync system can write records and remember where to resume next time.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced calendar event into readable text for search or recall. It makes the event look like a short note with a title, time, location, attendees, and description.

**Data flow**: It receives a record and the stream it came from. If the stream is not `calendar_events`, it falls back to the parent connector's default rendering. For calendar events, it safely extracts the title, time range, location, attendee email addresses, and description, then returns a title plus a formatted text body.

**Call relations**: The wider system calls this after records have been synced and need to become human-readable content. It uses `_str` to avoid treating non-text values as titles, then builds the event summary itself. Attendee-only records are not specially rendered here; they are passed back to the base connector behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the simpler event record used by this system. It keeps the useful fields and normalizes details such as organizer email, start time, end time, and attendees.

**Data flow**: It receives a raw event dictionary from Google's API. It reads fields like ID, creation time, update time, summary, description, location, organizer, recurrence ID, and attendee list. It lowers email addresses where appropriate, converts Google time fields through `_parse_when`, converts attendees through `_attendee`, and returns one clean event dictionary.

**Call relations**: This helper is called by `GoogleCalendarConnector.paginate` when syncing the main `calendar_events` stream. It relies on `_attendee` for each invited person and `_parse_when` for start and end times, then hands the finished event record back to the pagination loop for delivery to the sync system.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Builds the small attendee summary that is stored inside a calendar event record. It keeps the attendee's email, display name, and invitation response in the system's preferred wording.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee email, copies the display name, translates Google's response value such as `needsAction` into the local value `needs_action`, and returns a compact attendee dictionary.

**Call relations**: This is used inside `_flatten_event` while building the attendee list embedded in an event. It does not talk to Google or the sync system directly; it is a small cleanup step in the event-shaping process.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one Google Calendar event into separate rows, one for each attendee. This makes invitees searchable and linkable as their own records instead of only being buried inside the event.

**Data flow**: It receives a raw Google event dictionary. It reads the event ID, organizer, timestamps, and attendee list. For every attendee with an email address, it creates a row with an ID made from the event ID and attendee email, plus the attendee's role, response, display name, and whether the attendee represents the calendar owner. It returns a list of these attendee rows.

**Call relations**: This helper is called by `GoogleCalendarConnector.paginate` when the requested stream is `event_attendees`. It asks `_attendee_role` to decide whether each person is the organizer, a resource, optional, or required, then gives the completed rows back to the pagination loop.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in an event. It translates Google's attendee flags into a simple role label such as organizer, resource, optional, or required.

**Data flow**: It receives one attendee dictionary and a separate yes-or-no value saying whether that attendee matches the organizer. It checks organizer markers first, then resource and optional flags, and returns a single role string. If none of those flags apply, it returns `required`.

**Call relations**: This is called by `_flatten_attendees` while creating the per-attendee stream. It is the small rulebook that turns Google's raw attendee markers into the role value stored in each attendee row.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar time fields into one timestamp-like string format. It handles both timed events and all-day events.

**Data flow**: It receives a value that may be Google's time object. If the value contains `dateTime`, it returns that exact date-time string. If it contains an all-day `date`, it turns that date into a midnight UTC-style timestamp string. If the input is not a usable time object, it returns nothing.

**Call relations**: This is called by `_flatten_event` for an event's start and end fields. It hides the difference between Google's two time formats so event records can store `starts_at` and `ends_at` consistently.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents unexpected non-text values from being used as rendered event titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: This is called by `GoogleCalendarConnector.render` before building the readable event text. It is a small safety helper that keeps rendering simple and predictable.

*Call graph*: called by 1 (render).


### Drive-backed documents
Connectors that discover Google Drive resources and extract readable content from Docs, Drive metadata, and Sheets.

### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `source sync`

Google Docs are not returned as simple text files. First, Google Drive must be asked which Docs exist, and then the Google Docs API must be asked for the full contents of each one. This file is the connector that performs that two-step trip.

It starts by asking Drive for untrashed files whose type is “Google Doc.” If the sync has already run before, it uses a saved update time as a cursor, meaning it asks only for documents changed after that point. This keeps later syncs smaller and faster. Drive results are paged, like reading a long catalog one page at a time.

For every file Drive returns, the connector fetches the matching full document from the Docs API. If a document appears in Drive but cannot be opened, the connector keeps a small placeholder instead of failing the whole sync. That way, one missing or private document does not stop all other documents from being imported. But if the account is not allowed to list documents at all, the stream is skipped with a clear reason.

Finally, the connector renders each document into readable prose. Google stores document text inside a nested body tree, so the helper walks paragraph text runs in order and joins them into the text a person would expect to read.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main sync loop for Google Docs. It gathers Drive file records, fetches each full Google Doc, combines both pieces of information into one record, and yields records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor time from the previous sync. It asks `_iter_doc_files` for pages of Google Doc files, then asks `_document` for the full contents of each file. It builds normalized document records with IDs, titles, links, created and updated times, document content, and the original Drive metadata. It outputs batches of up to 100 document records, and if Drive or Docs access is refused for the whole stream, it raises `StreamSkipped` so the runner can skip this source cleanly.

**Call relations**: During a sync, the wider source runner calls this method to get Google Docs records page by page. `paginate` delegates file discovery to `GoogleDocsConnector._iter_doc_files` and per-document fetching to `GoogleDocsConnector._document`. If Google refuses access at the stream level, it creates a `StreamSkipped` error to explain that the connected account is missing permission or access.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one full Google Doc from the Google Docs API. It is deliberately forgiving when one listed document cannot be opened, so a single bad file does not ruin the entire sync.

**Data flow**: It receives an HTTP client and a Google Drive file ID. It requests the document data from the Docs API. If the request succeeds, it returns the full document object. If Google says the document is forbidden or not found, it returns a minimal record containing only the document ID. Other errors are passed upward unchanged.

**Call relations**: `GoogleDocsConnector.paginate` calls this after Drive has supplied a file ID. The result is folded into the larger sync record. This function is the safety valve for per-document failures: it lets the main sync continue when only one document is unavailable.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through Google Drive’s list of Google Docs that the connected account can see. It supports incremental sync by asking Drive only for files modified after the saved cursor time.

**Data flow**: It receives an HTTP client and an optional cursor. It builds a Drive search query for untrashed Google Docs, adds a modified-time filter if a cursor exists, and requests Drive pages ordered by modification time. Each Drive response is cleaned with `list_or_empty` so missing or malformed file lists become an empty list instead of crashing. It yields each non-empty page of file metadata, then follows `nextPageToken` until Drive says there are no more pages.

**Call relations**: `GoogleDocsConnector.paginate` calls this first to discover which documents need to be fetched. `_iter_doc_files` relies on `list_or_empty` to safely interpret Drive’s `files` field before handing those file lists back to the main pagination loop.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a title and a plain-text body suitable for display, indexing, or recall. It adds a simple heading so the text still has context outside Google Docs.

**Data flow**: It receives one document record and the stream description it came from. It reads the title if present, asks `_plain_text` to extract readable text from the document body, then returns two things: the title and a rendered text block with a heading followed by the document text.

**Call relations**: After records have been fetched by the connector, the sync system can call `render` when it needs human-readable prose. `render` hands the difficult document-body extraction to `_plain_text`, then wraps the result with a clear source heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable words from Google’s nested document format. It focuses on paragraph text and ignores structures that do not contain ordinary paragraph runs.

**Data flow**: It receives a document record. It looks for `body.content`, then walks each content item, each paragraph, and each paragraph element. Whenever it finds a text run with string content, it collects that text. It returns all collected text joined in order and trimmed at the edges.

**Call relations**: `GoogleDocsConnector.render` calls this when it needs to turn a raw Google Docs API response into plain prose. It is the small text-extraction helper that makes the rendered output readable instead of exposing Google’s nested API shape.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `source sync / request handling`

This connector is the bridge between Google Drive and the UFO source-sync system. Without it, the system would not know which Google Drive API endpoints to call, how to page through large result sets, or how to notice that a file was deleted or moved to trash.

The main idea is that Google Drive data is read as several “streams,” meaning named feeds of records. The most important stream is `files`. On the first run, the connector lists all untrashed files in modified-time order. After that first scan, it asks Google for a changes token, which works like a bookmark. On later runs, it uses that bookmark to fetch only what changed. If Google says the bookmark is too old, the connector raises a clear “cursor expired” signal so the wider system can start fresh instead of silently missing data.

Other streams work differently. Shared drives are fully reread each time. Permissions, comments, and revisions are child collections, so the connector first lists files and then asks Google for each file’s related items. If access is missing, it records a skipped stream rather than treating the whole sync as broken.

The file also includes a small renderer that turns a Drive file record into readable text with its name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main dispatcher for reading a Google Drive stream. Given a stream name, it chooses the right reading strategy: files, file changes, shared drives, or per-file child data like comments.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks which stream is being requested, then yields pages of records or special stream pages containing records, deletions, and the next cursor. If Google replies with a permission error, it turns that into a skipped stream so the larger sync run can continue cleanly.

**Call relations**: The sync system calls this when it wants records for a Google Drive stream. It hands the real work to `_paginate_files`, `_paginate_file_changes`, `_paginate_shared_drives`, `_paginate_file_children`, or `_start_page_token` depending on the stream and whether a cursor already exists.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Google Drive file records from the normal files listing endpoint. It is used for the first full file scan and also as the starting point for finding each file’s permissions, comments, or revisions.

**Data flow**: It receives an HTTP client and an optional modified-time cursor. It builds a Google Drive query for untrashed files, optionally only files newer than the cursor, then repeatedly requests pages from Google. Each response’s `files` list is cleaned into an empty list when missing, and non-empty pages are yielded until Google has no next page token.

**Call relations**: `paginate` calls this for the main `files` stream when there is no changes cursor. `_paginate_file_children` also calls it so it can visit every file before asking for that file’s child records.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current changes bookmark after a full file scan. That bookmark lets the next sync read only future changes instead of listing every file again.

**Data flow**: It receives an HTTP client, calls Google’s start-page-token endpoint, and reads the `startPageToken` field from the response. If the token is a non-empty string, it returns it; otherwise it returns nothing.

**Call relations**: `paginate` calls this after the first full `files` listing is complete. The returned token is wrapped in a stream page and passed back to the sync engine as the next cursor.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads incremental file changes from Google Drive using a saved cursor. It detects updated files, deleted files, and trashed files so the local copy can be kept in step with Drive.

**Data flow**: It receives an HTTP client and a required cursor token. For each changes page, it asks Google for changes, separates live file records from deleted or trashed file IDs, and yields a `StreamPage` containing records, deletes, and the next cursor. If Google says the token has expired, it raises `CursorExpired` so the system knows it must rescan.

**Call relations**: `paginate` calls this whenever the `files` stream already has a cursor. It creates the stream pages that tell the wider sync process both what to upsert and what to tombstone.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the list of shared drives available to the connected Google account. Shared drives are reread from scratch each run because this stream does not use the same changes-token flow as files.

**Data flow**: It receives an HTTP client, requests shared-drive pages from Google, extracts the `drives` list from each response, and yields non-empty pages. It follows Google’s `nextPageToken` until there are no more pages.

**Call relations**: `paginate` calls this when the requested stream is `shared_drives`. It supplies complete shared-drive pages directly back to the sync engine.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads per-file child collections: permissions, comments, or revisions. It works file by file, like checking every folder label in a filing cabinet and then reading the notes attached to each document.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. First it gets all files through `_paginate_files`. For each valid file ID, it calls the matching child endpoint, follows child-page tokens, optionally filters child records by the stream’s cursor field, and yields records enriched with the parent file’s ID and name. If Google refuses access to one file’s child data with common child-level errors, it skips that file’s child collection and continues.

**Call relations**: `paginate` calls this for `permissions`, `comments`, and `revisions`. This function depends on `_paginate_files` to know which files exist before it can request their child records.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a short human-readable text body. That text can be indexed or displayed more usefully than raw JSON.

**Data flow**: It receives a record and its stream description. For non-file streams, it delegates to the base connector’s renderer. For file records, it extracts the title, MIME type, owners, and web link, then returns a title plus a simple multi-line description.

**Call relations**: The broader source framework calls this when it needs a readable representation of a synced record. For Drive files, it uses `_str` to safely turn the file name into a string before building the output.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only if it is already a string. It prevents non-text values from accidentally becoming file titles.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: `GoogleDriveConnector.render` calls this while preparing a readable title for a Drive file record.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `sync pagination and record rendering`

Google Sheets data is split across two Google services: Drive knows which spreadsheet files exist and when they changed, while Sheets knows the tabs and cell values inside each file. This connector joins those two views. It first asks Drive for spreadsheet files, ordered by their modified time, then asks Sheets for each spreadsheet's tab list and, when needed, the rows in each tab.

The file supports three streams: one record per spreadsheet, one record per tab, and one record per tab's grid of values. It syncs incrementally, meaning it remembers a cursor, like a bookmark, so later runs can ask Drive only for files changed at or after the last seen time. The cursor can also carry file IDs that were temporarily refused, so a later run can try them again without blocking progress for everything else.

A lot of the care here is about failures. If the whole Google grant is missing permission, the stream is skipped cleanly. If just one file or tab is inaccessible, the connector records what it can and remembers the refused item. Quota errors are treated as real run failures. The render method then turns raw spreadsheet records into readable text for the rest of the system.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 162–212)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main read loop for the connector. It produces pages of spreadsheet, sheet, or sheet-value records while keeping a cursor that says how far the sync has safely progressed.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. It decodes the cursor into a modified-time bookmark and any previously refused file IDs, lists changed spreadsheets from Drive, turns each visited file into stream records, and yields pages with a new cursor. If some files were refused before, it tries them after the normal listing and updates the carried refusal list.

**Call relations**: The source runner calls this when it wants records. Inside the run, it asks _spreadsheet_visits for normal Drive-listed files, asks _visit_records to turn each file visit into the requested stream shape, uses _carried_visit for retrying refused files, and relies on cursor helpers to store progress. If a broad permission refusal reaches this level, it raises StreamSkipped so the run records a clean skip instead of pretending the stream synced.

*Call graph*: calls 9 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _error_detail, _is_quota_refusal, _settled); 1 external calls (__init__).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 214–239)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists spreadsheet files from Google Drive in pages. It is the connector's doorway into Drive's file catalog.

**Data flow**: It receives an HTTP client and an optional watermark time. It builds a Drive search query for untrashed Google spreadsheet files, adds the watermark as a modified-time filter when present, follows Drive page tokens, and yields batches of file metadata.

**Call relations**: _spreadsheet_visits calls this to get the raw Drive file batches. It normalizes Drive's files list with list_or_empty so later code can loop safely even if Google omits or malforms that field.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 241–249)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This turns Drive-listed spreadsheet files into richer file visits. A visit is the connector's bundle of a file ID, the record built for that file, and whether the file was refused.

**Data flow**: It receives an HTTP client and watermark, reads batches from _iter_spreadsheet_files, skips entries without a usable file ID, and asks _file_visit to fetch Sheets metadata for each spreadsheet. It yields one _FileVisit per usable file.

**Call relations**: paginate uses this during the normal Drive listing phase. It sits between the raw Drive listing and _file_visit, making sure only real spreadsheet IDs move on to the Sheets API lookup.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 251–265)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This retries a spreadsheet file that was refused in an earlier run. It lets the connector recover later if access to a file is granted after the first attempt.

**Data flow**: It receives an HTTP client and a file ID from the carried cursor. It asks Drive for that file's current metadata, including whether it is trashed. If the file is gone or still refused in a per-file way, it returns a visit with no record or a still-refused marker. If the file is readable and not trashed, it passes the metadata to _file_visit.

**Call relations**: paginate calls this after finishing the normal listing, but only for carried IDs not already seen in that listing. It uses _error_detail and _is_per_file_refusal to decide whether a failure belongs to this one file or should be raised as a larger problem.

*Call graph*: calls 3 internal fn (_file_visit, _error_detail, _is_per_file_refusal); called by 1 (paginate); 1 external calls (__init__).


##### `GoogleSheetsConnector._file_visit`  (lines 267–294)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This builds the main spreadsheet record for one Drive file. It combines Drive facts, like modified time and web link, with Sheets facts, like title and tab list.

**Data flow**: It receives an HTTP client, a file ID, and Drive metadata. It asks the Sheets API for spreadsheet metadata without grid cell data. If that specific file is refused, it falls back to a minimal record using the Drive file name. It returns a _FileVisit containing the assembled record and a refusal flag.

**Call relations**: _spreadsheet_visits uses this for files found in Drive, and _carried_visit uses it for retried files. It calls _error_detail and _is_per_file_refusal so only per-file permission problems become partial records; broader API or quota problems are left for callers to handle.

*Call graph*: calls 2 internal fn (_error_detail, _is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 1 external calls (__init__).


##### `GoogleSheetsConnector._visit_records`  (lines 296–312)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This converts one visited spreadsheet into records for whichever stream is being synced. It is the dispatcher that separates spreadsheet-level, tab-level, and cell-value output.

**Data flow**: It receives an HTTP client, a stream description, and a _FileVisit. If the visit has no record, it returns no records but preserves the refusal status. For the spreadsheets stream it returns the spreadsheet record, for the sheets stream it expands tabs with _sheet_records, and for sheet_values it fetches grids with _sheet_value_records.

**Call relations**: paginate calls this for every normal and carried visit. It hands tab expansion to _sheet_records and hands row fetching to _sheet_value_records, then reports back both the records and whether anything was refused.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 314–370)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This reads the cell rows for every tab in a spreadsheet. It creates one record per tab's grid so the system can recall the actual sheet contents, not just the tab names.

**Data flow**: It receives an HTTP client and a spreadsheet record. It extracts tab titles and sheet IDs, requests tab ranges from the Sheets values API in bounded batches, checks that Google returned one answer per requested tab, and builds records with _sheet_value_record. If a whole batch is refused for a per-file reason, it retries tabs one by one so only the refused tab is dropped.

**Call relations**: _visit_records calls this only for the sheet_values stream. It uses _quoted_sheet_range to name tabs safely, _error_detail and _is_per_file_refusal to classify refusals, list_or_empty to read Google's valueRanges safely, and StreamFault when Google's batch response shape does not match the request.

*Call graph*: calls 5 internal fn (__init__, _error_detail, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 2 external calls (list_or_empty, quote).


##### `GoogleSheetsConnector.render`  (lines 372–391)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns synced Google Sheets records into readable text. That text is what a person or recall system can understand without reading raw API JSON.

**Data flow**: It receives one record and its stream description. For spreadsheet records, it makes a heading and lists tab names. For sheet records, it names the parent spreadsheet. For sheet-value records, it turns rows into plain text using _grid_text. It returns a title and a rendered body.

**Call relations**: The broader source framework calls render when it needs human-readable content from records. This method uses _str to safely extract text fields and _grid_text to format cell grids, while falling back to the parent renderer for unknown streams.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 394–407)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This reads the saved sync bookmark for the connector. It understands both old simple cursors and the newer JSON cursor that can also carry refused file IDs.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns an empty starting point. If the cursor is plain text or non-JSON, it treats it as just the watermark. If it is valid checkpoint JSON, it returns the watermark, refused IDs, and last retried ID.

**Call relations**: paginate calls this at the start of a run. Its output controls where Drive listing starts and which refused files will be retried later.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 410–415)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This writes the connector's sync bookmark. It keeps the normal modified-time watermark and, when needed, adds a small list of refused file IDs to try again later.

**Data flow**: It receives a watermark, a set of refused IDs, and an optional retried ID. If there is no watermark or no refused IDs, it returns the plain watermark. Otherwise it creates checkpoint JSON with the refused IDs sorted and capped.

**Call relations**: paginate calls this whenever it yields a page or final checkpoint. The cursor it creates is later read by _decode_cursor on the next run.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 418–419)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This updates the set of files that still need retrying. It answers the simple question: after this attempt, is this file still refused?

**Data flow**: It receives the current refused-ID set, one file ID, and a boolean saying whether that file is still refused. If still refused, the file is added to the set. If not, it is removed. The returned set becomes the new carry list.

**Call relations**: paginate uses this after each normal or carried visit is turned into records. It keeps the checkpoint honest so fixed permissions remove files from the retry list.

*Call graph*: called by 1 (paginate).


##### `_error_detail`  (lines 422–427)

```
def _error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This extracts the useful Google error body from an HTTP failure. It gives the rest of the connector a safe dictionary to inspect instead of raw response text.

**Data flow**: It receives an HTTP status error. It tries to parse the response as JSON, then pulls out the nested error object if present. If parsing fails or the shape is not a dictionary, it returns an empty dictionary.

**Call relations**: paginate, _carried_visit, _file_visit, and _sheet_value_records call this before classifying failures. The refusal-checking helpers then inspect the returned detail.

*Call graph*: called by 4 (_carried_visit, _file_visit, _sheet_value_records, paginate); 1 external calls (dict_or_empty).


##### `_is_per_file_refusal`  (lines 430–436)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This decides whether an HTTP failure is about one specific spreadsheet or tab, rather than the whole Google account grant or the whole service. That distinction lets the connector skip only the affected file when safe.

**Data flow**: It receives an HTTP status code and parsed Google error detail. It returns true only for file-level fallback statuses with a real Google error body, excluding quota failures and broad grant failures.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records use this when deciding whether to fall back, retry individually, or raise the error. It delegates the two exclusions to _is_quota_refusal and _is_grant_refusal.

*Call graph*: calls 2 internal fn (_is_grant_refusal, _is_quota_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records).


##### `_is_quota_refusal`  (lines 439–442)

```
def _is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects quota and rate-limit failures from Google. These are not treated as missing file access because trying to skip a file would hide a real capacity problem.

**Data flow**: It receives parsed Google error detail. It checks for Google's resource-exhausted status or known usage-limit reasons in the error list, and returns a boolean.

**Call relations**: paginate uses this before converting broad 401 or 403 errors into StreamSkipped. _is_per_file_refusal also uses it to make sure quota failures are raised instead of mistaken for one-file refusals.

*Call graph*: called by 2 (paginate, _is_per_file_refusal); 1 external calls (list_or_empty).


##### `_is_grant_refusal`  (lines 445–450)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects failures that mean the connected Google grant or project cannot use Drive or Sheets properly. In plain terms, the key or permission is wrong for the whole stream, not just one file.

**Data flow**: It receives parsed Google error detail. It checks known Google reasons such as missing configuration or insufficient permissions, and also checks Google service-domain details. It returns true when the refusal points to the grant or API setup.

**Call relations**: _is_per_file_refusal calls this to avoid treating whole-grant failures as per-file problems. That helps callers raise or skip the stream correctly instead of producing partial misleading data.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 453–474)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This expands one spreadsheet record into one record per tab. It gives each tab its own identity while preserving the parent spreadsheet's title and timestamps.

**Data flow**: It receives a spreadsheet record from _file_visit. It loops through the spreadsheet's sheets list, skips malformed tab entries, and builds records containing the tab data, a combined spreadsheet-and-tab ID, parent spreadsheet fields, and copied created/updated times.

**Call relations**: _visit_records calls this when the requested stream is sheets. Its output lets the rest of the sync treat tabs as separate recallable items.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 477–490)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This wraps one tab's returned cell values with the context needed to identify where they came from. It connects a value grid back to its spreadsheet and tab.

**Data flow**: It receives a spreadsheet record, tab title, sheet ID, and one valueRange response from Google. It copies the valueRange fields and adds a stable ID, spreadsheet ID and title, sheet ID and title, and the parent file's timestamps.

**Call relations**: _sheet_value_records calls this after each successful batch or single-tab values request. The resulting records become the sheet_values stream output.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 493–495)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This formats a sheet tab title as a safe Google Sheets range name. It matters because unquoted tab names can be mistaken for cell references or named ranges.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title, then wraps the whole title in apostrophes, producing an A1-style sheet reference.

**Call relations**: _sheet_value_records uses this before calling the Sheets values API. The same quoted range is also URL-encoded when falling back from batch reads to individual tab reads.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 498–503)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This turns a sheet's rows and cells into simple text. It makes grid values readable outside the spreadsheet interface.

**Data flow**: It receives a value that should be a list of rows. If it is not a list, it returns an empty string. Otherwise it keeps row lists, converts each cell to text, joins cells with " | ", and joins rows with newlines.

**Call relations**: render calls this for sheet_values records. It is the final formatting step that turns raw cell arrays into a plain text body.

*Call graph*: called by 1 (render).


##### `_str`  (lines 506–507)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns optional record fields into display text. It avoids showing non-text values where a title or label is expected.

**Data flow**: It receives any value. If the value is already a string, it returns it. Otherwise it returns an empty string.

**Call relations**: render uses this when building headings and short descriptions. It keeps rendered output clean even when Google omits a field or sends an unexpected shape.

*Call graph*: called by 1 (render).


### Meeting artifacts
Connector that converts Google Meet transcripts and generated notes into recallable meeting pages.

### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `sync run while fetching and rendering Google Meet source records`

This connector is like a careful meeting archivist. It asks Google Meet for recent conference records, checks each meeting for generated artifacts, then builds one readable page per meeting that has either a transcript or AI notes. It supports incremental syncing, which means it remembers the latest meeting start time it has seen and avoids rereading everything on every run. It also looks back by one day, because Google may create transcripts or notes after a meeting ends.

The file talks to two Google services. It uses the Meet API to list conferences, transcripts, transcript entries, and smart notes. When a smart note points to a Google Docs file, it also tries the Docs API to pull the note text directly. If Google refuses access to Meet with a 401 or 403 response, the stream is marked as skipped rather than crashing the whole sync. If a linked Doc is missing or forbidden, the connector keeps the document link and continues.

After collecting data, the connector renders it as plain prose: meeting details at the top, then transcript dialogue, then AI summaries. Small helper functions clean up Google resource names, format labels, combine transcript lines by speaker, and safely treat missing or unexpected fields as empty text.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main reader for the Google Meet stream. It walks through pages of conference records from Google, finds meetings that have transcripts or smart notes, and yields them to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It builds Google Meet request parameters, optionally adds a one-day lookback filter, fetches pages of conferences, expands each conference into a full meeting record, and outputs StreamPage objects containing only meetings with useful artifacts plus the next cursor.

**Call relations**: The sync driver calls this when it wants records from the Google Meet source. During the walk it asks _lookback to widen the cursor window, _max_start_time to advance the cursor, and _conference_record to fetch the details for each meeting. If Google refuses access, it raises StreamSkipped so the wider run can continue instead of treating the source as broken.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the full internal record for one Google Meet conference. It gathers the meeting’s transcripts and smart notes and packages them with the meeting’s basic times and identifiers.

**Data flow**: It receives one conference object from the Meet API. It reads the conference resource name, fetches transcript artifacts and smart-note artifacts under that conference, converts each artifact into a cleaner shape, and returns a dictionary representing the meeting page to be rendered later.

**Call relations**: paginate calls this for each conference it receives from Google. This function is the hub for a single meeting: it delegates artifact listing to _artifacts, transcript shaping to _transcript, smart-note shaping to _smart_note, and small cleanup work to _str and _resource_id.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This lists a particular kind of artifact under a meeting, such as transcripts or smart notes. It hides the repeated page-by-page API fetching needed when Google returns many items.

**Data flow**: It receives an HTTP client, a parent conference name, and a collection name. If the parent is missing it returns an empty list; otherwise it repeatedly asks Google for artifact pages, collects the returned items, follows any next-page token, and returns the combined list.

**Call relations**: _conference_record calls this twice for each meeting: once for transcripts and once for smart notes. It uses list_or_empty to safely turn absent or oddly shaped API fields into an ordinary empty list.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript artifact from Google into the connector’s simpler transcript record. It includes transcript metadata, any linked Google Docs destination, and the spoken entries inside the transcript.

**Data flow**: It receives a raw transcript object. It extracts the transcript name, state, times, and document destination, fetches all transcript entries for that transcript, and returns a dictionary ready to be placed inside a meeting record.

**Call relations**: _conference_record calls this for every transcript artifact found under a conference. It relies on _transcript_entries for the actual dialogue, _docs_destination for the linked document fields, and _resource_id and _str for safe identifiers and strings.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual spoken lines for a transcript. Each entry includes who spoke, what they said, and the time range when they said it.

**Data flow**: It receives an HTTP client and a transcript resource name. It requests entry pages from Google, converts each entry into a smaller dictionary with id, participant, text, language, and times, and returns the full list. If the transcript name is empty it returns nothing; if Google says the entries are missing or forbidden, it returns whatever it has gathered so far.

**Call relations**: _transcript calls this while building a transcript record. It uses _str and _resource_id to normalize entry names, and list_or_empty to safely read Google’s list of transcript entries.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one smart-note artifact into a cleaner record and, when possible, includes the note’s text. Smart notes are Google’s AI-generated meeting summaries.

**Data flow**: It receives a raw smart-note object. It extracts the note’s id, name, state, times, and linked Google Docs destination; if there is a document id, it tries to fetch the document’s plain text and adds it as the note body when available.

**Call relations**: _conference_record calls this for every smart-note artifact under a meeting. It uses _docs_destination, _resource_id, and _str for basic cleanup, and hands the linked document id to _document_text when it can inline the Google Doc content.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This tries to read plain text from a Google Docs document linked by a smart note. It lets meeting summaries be searchable as text instead of only appearing as links.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely places the id into a Docs API URL, fetches the document, and converts the structured document response into plain text. If the document is forbidden or missing, it returns an empty string instead of failing the sync.

**Call relations**: _smart_note calls this when a note points to a Google Doc. After fetching the document, it hands the structured response to _plain_text to pull out readable text.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a collected meeting record into the final text page stored by the system. It gives the page a title and lays out meeting details, transcripts, and AI summaries in a readable order.

**Data flow**: It receives a meeting record and the stream description. For the Google Meet meeting_artifacts stream, it reads the title and key fields, formats labeled metadata, asks helper functions to render transcript and smart-note sections, and returns the title plus the finished text. For any other stream it falls back to the parent renderer.

**Call relations**: The source framework calls this after records have been fetched. It uses _labeled for the meeting metadata, _transcripts_section for dialogue, _smart_notes_section for summaries, and _str to avoid accidental non-text values in the page title.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcripts for a meeting into a human-readable section. It shows transcript metadata and then the spoken dialogue.

**Data flow**: It receives a value that should contain transcript records. It safely treats missing or invalid values as an empty list, then for each transcript formats state, times, and document link, adds the dialogue text, and returns one combined Markdown-style section.

**Call relations**: GoogleMeetConnector.render calls this while building the final meeting page. It asks _labeled to format transcript metadata, _dialogue to turn entries into speaker lines, and _str and list_or_empty to keep messy API data from leaking into the rendered text.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats a meeting’s AI summaries into a readable section. It includes note metadata, the linked document URL, and the note body if the connector could fetch it.

**Data flow**: It receives a value that should contain smart-note records. It safely converts that value into a list, formats each note’s state, times, and document link, appends the body text when present, and returns one combined Markdown-style section.

**Call relations**: GoogleMeetConnector.render calls this when assembling the final meeting page. It uses _labeled for the note metadata and _str and list_or_empty to safely deal with missing or unexpected fields.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into a clean conversation transcript. When the same speaker has consecutive entries, it joins them into one line so the result reads more naturally.

**Data flow**: It receives a value that should be a list of transcript entries. It skips entries without text, derives a speaker label, joins repeated consecutive speaker lines, and returns newline-separated dialogue such as “Participant: hello”.

**Call relations**: _transcripts_section calls this to render the spoken part of each transcript. It uses _speaker to name each participant, _str to safely read text, and list_or_empty to handle missing entry lists.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the structured format returned by the Google Docs API. Google Docs sends paragraphs and text runs as nested data, and this function flattens them into normal text.

**Data flow**: It receives a Google Docs document response as a dictionary. It walks through the document body, looks inside paragraph elements for text runs, collects their text content, joins the pieces, trims extra space at the ends, and returns the resulting string.

**Call relations**: _document_text calls this after successfully fetching a Google Doc. It does not fetch anything itself; it only converts the already-received document structure into plain text.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls out the Google Docs destination from a transcript or smart-note artifact. That destination is important because it gives a durable document id and a link even when inline content cannot be fetched.

**Data flow**: It receives an artifact record. If the record contains a docsDestination object, it returns a small dictionary with the document id and export URL as strings; otherwise it returns an empty dictionary.

**Call relations**: _transcript and _smart_note call this while shaping artifacts into the connector’s internal format. It uses _str so missing or non-text document fields become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest meeting start time seen so far. That value becomes the cursor used to avoid rereading old conferences on future syncs.

**Data flow**: It receives a list of conference records and the previous cursor. It compares each conference’s startTime string with the current cursor value and returns the largest start time it finds, or the original cursor if nothing newer appears.

**Call relations**: paginate calls this after fetching a page of conferences. Its result is placed into the StreamPage so the sync driver knows where to resume next time.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor backward by one day. The connector uses this safety window because transcripts and AI notes can appear after a meeting has already ended.

**Data flow**: It receives an ISO-style timestamp string from the cursor. It parses it as a datetime, subtracts the configured one-day lookback, formats it back as a timestamp ending in Z, and returns that string for the Meet API filter.

**Call relations**: paginate calls this when it has a cursor and needs to build the next Google Meet query. This lets the connector refetch a recent window without rescanning the entire history.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the final id-like part from a Google resource name. For example, it turns a long path-style name into just the last segment.

**Data flow**: It receives a resource name string. If the string is present, it splits at the last slash and returns the final piece; if the string is empty, it returns an empty string.

**Call relations**: Several record-building helpers call this when they need stable, compact ids for conferences, transcripts, notes, entries, or speakers. _speaker also uses it to turn participant resource names into readable labels.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses the label shown before a transcript line. It uses the participant’s resource id when available and falls back to “Participant” when Google did not provide a usable name.

**Data flow**: It receives the participant field from a transcript entry. It safely converts it to text, extracts the final resource id, and returns that id or the default label “Participant”.

**Call relations**: _dialogue calls this for each transcript entry while building the readable conversation. It relies on _str for safe text conversion and _resource_id for shortening Google’s resource name.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only if it is already text. It prevents unexpected numbers, dictionaries, or missing values from being printed into rendered pages as confusing Python-style data.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Many functions use this at the edges where data comes from Google or is about to be rendered. It supports _conference_record, _transcript, _transcript_entries, _smart_note, render, _dialogue, _docs_destination, and the section-formatting helpers.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats simple metadata lines such as “start: 2024-...” while leaving out empty fields. It keeps rendered pages tidy and avoids blank labels.

**Data flow**: It receives a list of label-and-value pairs. It keeps only pairs with a non-empty value, formats each as “label: value”, joins them with newlines, and returns the finished block.

**Call relations**: GoogleMeetConnector.render uses this for top-level meeting metadata, while _transcripts_section and _smart_notes_section use it for artifact metadata. It is one of the final formatting helpers before text is written into the searchable page.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).

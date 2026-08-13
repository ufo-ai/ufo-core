# Google Workspace Source Connectors  `stage-14.1.1`

This stage is the Google Workspace “intake” layer. It runs when the system syncs connected accounts, not to change anything, but to read useful information and reshape it into plain text and records that the rest of the system can search and recall later.

Each connector is like an adapter for a different Google tool. The Gmail connector reads mailbox messages, extracts searchable text, and remembers Gmail’s change markers so future syncs only pick up new or changed mail. Google Calendar reads the primary calendar, turns events into records, and also creates attendee records so invitations can be understood person by person. Google Docs reads accessible documents and converts their contents into text. Google Drive covers the wider file system: files, shared drives, permissions, comments, and revisions. Google Meet reads meeting artifacts such as transcripts and generated notes, making meetings searchable. Google Sheets finds accessible spreadsheets and breaks them into spreadsheet, tab, and cell-value records. Together, these files turn Google Workspace from separate apps into consistent read-only streams for the system.

## Files in this stage

### Mail and Calendar Streams
Connectors that read personal communication and scheduling data into searchable records.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync and record rendering`

Gmail does not store an email as one simple text field. Each message is a tree of MIME parts, which means the readable text may be split between plain-text and HTML sections, and those sections are encoded. This file is the Gmail connector: it talks to Gmail’s REST API, finds message IDs, fetches full message bodies, and reshapes them into records the rest of the system can store and recall.

On a first run, it backfills the mailbox by listing all message IDs. It then saves Gmail’s history ID, which works like a bookmark in Gmail’s change log. On later runs, it asks Gmail what was added or deleted since that bookmark. If the bookmark is too old, Gmail returns an error, and this connector tells the core system to start over cleanly.

The file also turns awkward Gmail payloads into readable prose. It pulls out useful headers such as From, To, Cc, and Subject, decodes message bodies, strips HTML when needed, and formats the result like an email a person would actually read. Without this file, Gmail sync would either miss incremental changes or store hard-to-read raw API data instead of useful message text.

#### Function details

##### `GmailConnector.paginate`  (lines 75–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Gmail messages. It decides whether to do a full mailbox scan or only fetch changes since the last saved Gmail history bookmark, then yields pages of records and deletions to the wider sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If there is no cursor, it asks for all message IDs; if there is a cursor, it asks Gmail for added and deleted messages since then. It fetches message bodies in batches, turns them into pages, and outputs StreamPage objects containing new records, tombstones for deletes, and the next cursor. If Gmail refuses access because the grant lacks the needed read permission, it turns that into a skipped stream instead of a failed run.

**Call relations**: The sync framework calls this when it wants Gmail data. It hands first-run work to GmailConnector._backfill, later incremental work to GmailConnector._history, and body fetching to GmailConnector._fetch_bodies. At the end of each batch it packages the result as StreamPage objects for the core sync code to consume.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 109–126)

```
async def _backfill(self, client: httpx.AsyncClient) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first full scan of a mailbox. It collects every Gmail message ID so the connector can fetch all messages once before switching to incremental updates.

**Data flow**: It receives an HTTP client and repeatedly calls Gmail’s message-list endpoint, following page tokens until there are no more pages. It gathers valid message IDs into a list, then asks GmailConnector._seed_history_id for a history bookmark based on the newest listed message. It returns the complete ID list and the bookmark for the next run.

**Call relations**: GmailConnector.paginate calls this when there is no existing cursor. After gathering IDs, it delegates cursor setup to GmailConnector._seed_history_id so future syncs can use Gmail’s change history instead of listing everything again.

*Call graph*: calls 1 internal fn (_seed_history_id); called by 1 (paginate).


##### `GmailConnector._seed_history_id`  (lines 128–140)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str]) -> str | None
```

**Purpose**: This finds the Gmail history bookmark to save after a full backfill. That bookmark tells the next run where to start reading Gmail’s change log.

**Data flow**: It receives the HTTP client and the list of message IDs found during backfill. If the list is empty, it returns no cursor. Otherwise it fetches the first message in minimal form and reads its historyId. If that message disappeared before it could be fetched, it quietly returns no cursor; otherwise it returns the history ID if Gmail provided one.

**Call relations**: GmailConnector._backfill calls this after listing message IDs. Its result is passed back up to GmailConnector.paginate, which puts it into the final page as the next cursor.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 142–177)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log from a saved history ID. It finds which messages were added and which were deleted since the last sync.

**Data flow**: It receives an HTTP client and a previous Gmail history ID. It calls Gmail’s history endpoint page by page, collects message IDs from added and deleted entries, keeps the latest history ID as the next cursor, and returns three things: IDs to fetch, IDs to delete, and the new cursor. If Gmail says the old history ID has expired, it raises CursorExpired so the core system can refetch from scratch.

**Call relations**: GmailConnector.paginate calls this during incremental sync. Inside the loop it uses _message_ids to pull clean IDs out of Gmail’s nested history entries, then returns the result to paginate so new messages can be fetched and deletions can be emitted.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 179–193)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns a list of Gmail message IDs into full message records. It asks Gmail for each message body and prepares it for storage.

**Data flow**: It receives an HTTP client and message IDs. For each ID, it fetches the full Gmail message. If a message vanished between the history check and the body fetch, it skips that one. For messages that still exist, it passes the raw Gmail response to _flatten_message and returns a list of flattened records.

**Call relations**: GmailConnector.paginate calls this after it knows which message IDs are newly added. This function hands the detailed Gmail payloads to _flatten_message, then gives paginate ready-to-yield records.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 195–214)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Gmail record into readable text for recall or display. Instead of dumping Gmail’s raw nested data, it formats the message like a normal email with headers and body.

**Data flow**: It receives a flattened record and a stream description. For the messages stream, it reads the subject, sender, recipients, and body fields, formats contacts and recipients, chooses the best available body text, and returns a title plus a clean prose document. For other streams, it falls back to the parent connector’s default rendering.

**Call relations**: The broader source system calls this when it needs a human-readable version of a synced record. It relies on _str, _format_contact, _format_recipients, and _message_body to clean individual pieces before joining them into the final text.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 217–226)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts message IDs from Gmail history entries. Gmail wraps each ID inside nested objects, so this helper pulls out just the useful string values.

**Data flow**: It receives a value that should be a list of history entries. It ignores entries that are missing or not shaped like Gmail message objects, collects non-empty string IDs, and returns a clean list of IDs.

**Call relations**: GmailConnector._history uses this while reading Gmail’s added and deleted history records. The helper keeps the history loop focused on change tracking instead of nested response cleanup.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 229–256)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This reshapes one raw Gmail message into the simple record format used by the sync system. It pulls important headers, addresses, labels, and decoded body text out of Gmail’s nested payload.

**Data flow**: It receives the raw JSON-like Gmail message. It reads selected headers, extracts plain-text and HTML bodies, parses the sender and recipients, copies useful metadata such as IDs and labels, decides whether the message is inbound or outbound, and returns one flat dictionary.

**Call relations**: GmailConnector._fetch_bodies calls this after fetching each full message. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 259–273)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail MIME payload for the readable message body. It looks for the first plain-text part and the first HTML part.

**Data flow**: It receives the nested payload tree from Gmail. It walks through the root and all child parts, decodes the first text/plain and text/html bodies it finds, and returns them as a pair: plain text first, HTML second.

**Call relations**: _flatten_message calls this while preparing a raw Gmail message for storage. Its inner walker does the tree traversal and uses _b64url_decode when it finds encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 263–270)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive worker inside _extract_bodies. It visits one MIME part, checks whether it contains a useful body, and then visits its children.

**Data flow**: It receives one part of Gmail’s payload tree. If the part is plain text or HTML and has encoded data, it decodes the data and stores it if that type has not already been found. Then it repeats the same process for each child part.

**Call relations**: _extract_bodies starts this walker at the top of the Gmail payload. Whenever the walker finds body data, it hands decoding to _b64url_decode.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 276–282)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body text encoding into normal text. Gmail stores body parts as URL-safe base64, which is a text-safe way to represent bytes.

**Data flow**: It receives an encoded string from Gmail. It adds any missing padding characters, decodes it with URL-safe base64 rules, converts the bytes to UTF-8 text, and returns the readable string. If decoding fails, it returns an empty string rather than crashing the sync.

**Call relations**: _extract_bodies.walk calls this when it finds encoded plain-text or HTML message content. It uses Python’s base64.urlsafe_b64decode to do the low-level decoding.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 285–292)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This reads the first email address from a header such as From. It separates the email handle from the display name.

**Data flow**: It receives a header string or nothing. If there is a header, it asks the email address parser to split it into name-and-address pairs, takes the first pair, lowercases the address, and returns the address plus the display name. If nothing usable is present, it returns two empty values.

**Call relations**: _flatten_message calls this for the sender header. It uses email.utils.getaddresses so it can handle common email header formats like “Jane Doe <jane@example.com>”.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 295–302)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses recipient headers into a list of structured contacts. It is used for fields like To and Cc, which may contain several people.

**Data flow**: It receives a header string or nothing. It parses all addresses in the header, drops entries without an address, lowercases each address, keeps each display name if present, and returns a list of small contact dictionaries.

**Call relations**: _flatten_message calls this when building the record’s To and Cc fields. Like _parse_first_address, it relies on email.utils.getaddresses to understand normal email header syntax.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 305–310)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable email label. It prefers the friendly form “Name <address>” when a display name exists.

**Data flow**: It receives a possible email handle and display name. If the handle is missing or not text, it returns an empty string. If there is a display name, it combines name and handle; otherwise it returns just the handle.

**Call relations**: GmailConnector.render uses this for the sender line, and _format_recipients uses it for each recipient. It is the small formatting rule that keeps contact display consistent.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 313–320)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This turns a list of recipient contact records into one readable comma-separated line. It is used for email headers like To and Cc.

**Data flow**: It receives a value that should be a list of contact dictionaries. If it is not a list, it returns an empty string. Otherwise it formats each dictionary with _format_contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this while building the human-readable header block. It delegates individual contact formatting to _format_contact.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 323–331)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for a Gmail record. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail’s snippet if no full body is available.

**Data flow**: It receives a flattened message record. It first checks body_text and returns it if non-empty. If not, it checks body_html and converts it to plain text. If neither is useful, it returns the snippet text if present, or an empty string.

**Call relations**: GmailConnector.render calls this when composing the final readable email document. When HTML is the only available body, it relies on _HtmlText.extract to strip tags and keep readable words.

*Call graph*: called by 1 (render).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a maybe-string value into a string for rendering. It avoids accidentally treating non-text values as email text.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render uses this for the subject before formatting the rendered message. It is a small guardrail around data that came from an external API.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 342–344)

```
def __init__(self) -> None
```

**Purpose**: This creates a fresh HTML-to-text parser. It prepares an empty list where readable text pieces will be collected.

**Data flow**: It receives no external data beyond the new object being created. It initializes the base HTML parser with automatic character-reference conversion, then sets up internal storage for text fragments. The result is a parser object ready to receive HTML.

**Call relations**: _HtmlText.extract creates an instance of this parser before feeding it HTML. The parser’s later callback methods add text and line breaks into the storage prepared here.


##### `_HtmlText.extract`  (lines 347–352)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It removes tags, keeps the words, and preserves useful line breaks around block-like HTML elements.

**Data flow**: It receives raw HTML text. It feeds that HTML into a new _HtmlText parser, joins the collected text fragments, trims repeated whitespace on each line, removes blank lines, and returns clean plain text.

**Call relations**: _message_body uses this when a message has HTML but no plain-text body. During parsing, the parser’s handle_data, handle_starttag, and handle_endtag methods are invoked by the HTML parsing machinery.


##### `_HtmlText.handle_data`  (lines 354–355)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records ordinary text found inside HTML. It is how the parser keeps the words while ignoring the markup around them.

**Data flow**: It receives a chunk of character data from the HTML parser. It appends that chunk to the parser’s internal list. Nothing is returned; the parser’s collected text is changed.

**Call relations**: When _HtmlText.extract feeds HTML into the parser, the HTML parser calls this method for text between tags. Those collected chunks later become the plain-text email body.


##### `_HtmlText.handle_starttag`  (lines 357–359)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag usually means a visible block break. For example, a paragraph or list item should not run into the previous sentence.

**Data flow**: It receives an HTML tag name and its attributes. If the tag is one of the known block-style tags, it appends a newline marker to the parser’s internal text pieces. It ignores tag attributes and returns nothing.

**Call relations**: During _HtmlText.extract, the HTML parser calls this as it sees opening tags. It works with handle_endtag and handle_data to turn HTML layout into readable plain text.


##### `_HtmlText.handle_endtag`  (lines 361–363)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a closing HTML tag ends a visible block. It helps keep separate paragraphs, table cells, and headings from being mashed together.

**Data flow**: It receives an HTML tag name. If the tag is one of the known block-style tags, it appends a newline marker to the parser’s internal text pieces. It returns nothing and only changes the parser’s collected output.

**Call relations**: During _HtmlText.extract, the HTML parser calls this as it sees closing tags. Together with handle_starttag and handle_data, it produces the cleaned text that _message_body can use.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the rest of the UFO source-sync system. Without it, calendar events would stay inside Google and could not be indexed, remembered, or linked to attendees by this project.

The connector reads from Google's events API for the user's primary calendar. On the first run, it looks back over a fixed recent window and asks Google for all relevant events, including deleted ones. Google then gives back a sync token, which works like a bookmark. On later runs, the connector sends that bookmark back to Google and receives only what changed since last time. This avoids rereading the whole calendar every run.

The file exposes two streams. The first, `calendar_events`, stores one record per event, including title, time, location, description, organizer, and attendee summaries. The second, `event_attendees`, breaks the same event apart into one row per invitee, like making a guest list from a party invitation.

It also deals with important failure cases. If Google's bookmark has expired, it raises `CursorExpired` so the wider sync system can start fresh. If the user did not grant Calendar permission, it raises `StreamSkipped`, meaning the run records a clean skip instead of treating it as a broken connector. The connector only reads calendar data; it never writes changes back to Google.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads Google Calendar events page by page and turns them into sync pages for the rest of the system. It supports both event records and attendee records, and it uses Google's sync token as a bookmark so later runs only fetch changes.

**Data flow**: It receives an HTTP client, a stream choice, and an optional cursor from a previous run. It builds Google API query parameters, fetches event pages, skips malformed items, converts active events into records, and records cancelled events as deletes for the event stream. It yields `StreamPage` objects containing new or changed records, deleted IDs, and finally the next cursor to save for the next run. If Google says the cursor is too old, it turns that into a cursor-expired signal; if access is refused because Calendar permission is missing, it turns that into a skipped stream.

**Call relations**: This is the main read loop called by the source-sync framework when it wants data from Google Calendar. During the loop it hands raw events to `_flatten_event` for the `calendar_events` stream, or to `_flatten_attendees` for the `event_attendees` stream. It packages those results into `StreamPage` objects so the core sync code can store records, remove tombstoned events, and remember the next sync token.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced calendar event into readable text for recall or display. It gives the system a plain body that includes the event title, time, location, attendees, and description.

**Data flow**: It receives one stored record and the stream it belongs to. For normal calendar events, it extracts safe text fields, builds a small human-readable document, and returns both a title and body text. For any stream other than `calendar_events`, it falls back to the parent connector's default rendering behavior.

**Call relations**: The wider system calls this after records have been synced and need to become searchable or presentable content. It uses `_str` to safely treat non-text titles as empty text, then formats the event details into a simple note-like shape.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the project's standard event record. It keeps the useful calendar details and folds a simple attendee list into the event so the event can be recalled with context about who was invited.

**Data flow**: It receives a raw event dictionary from Google's API. It reads fields such as ID, creation time, update time, summary, description, location, start and end times, organizer, recurrence information, and attendees. It normalizes attendee entries through `_attendee` and normalizes start and end time values through `_parse_when`, then returns one flat dictionary ready to be written as a `calendar_events` record.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for each non-cancelled event when syncing the `calendar_events` stream. Inside this conversion, `_attendee` prepares each attendee summary and `_parse_when` makes Google's two different time formats look consistent to the rest of the system.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Builds a small, clean attendee summary from one Google attendee object. It keeps the person's email address, display name, and invitation response in the system's preferred wording.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the email address to make matching consistent, copies the display name if present, translates Google's response status into the project's response names, and returns a compact dictionary.

**Call relations**: `_flatten_event` calls this while building an event record's embedded attendee list. This keeps attendee cleanup in one place so event records do not have to repeat the same email and response-normalization logic.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one calendar event into separate attendee records, one per invitee. This lets the system store the guest list as its own relationship-style stream instead of only hiding it inside the event.

**Data flow**: It receives a raw Google event dictionary. It finds the event ID and organizer, then walks through the event's attendee list. For each valid attendee with an email address, it creates a record with a unique ID made from the event ID and email address, copies event timestamps, stores the attendee's role, response, display name, and whether the attendee is the calendar owner, then returns the full list of attendee rows.

**Call relations**: `GoogleCalendarConnector.paginate` calls this when syncing the `event_attendees` stream. For each attendee row, it asks `_attendee_role` to decide whether the person is the organizer, a resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in an event. It turns several Google flags into one simple role label the rest of the system can understand.

**Data flow**: It receives one attendee dictionary and a separate flag saying whether this attendee matches the organizer's email address. It checks, in order, whether the attendee is the organizer, a resource such as a room, an optional guest, or otherwise a required guest. It returns one role string.

**Call relations**: `_flatten_attendees` calls this while creating the separate attendee stream. Its answer becomes the `role` field on each attendee record, making the invitee list easier to query and interpret later.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google's event time formats into one timestamp-like string. Google uses one shape for timed events and another for all-day events, and this helper smooths over that difference.

**Data flow**: It receives an unknown value that may be a Google start or end time object. If it is not a dictionary, it returns nothing. If it contains `dateTime`, it returns that value as text. If it contains an all-day `date`, it turns the date into a midnight UTC-style timestamp string. If neither format is present, it returns nothing.

**Call relations**: `_flatten_event` calls this for event start and end values. That means stored event records can use `starts_at` and `ends_at` without every later reader needing to understand Google's two separate time formats.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents the renderer from accidentally treating numbers, missing values, or other objects as event titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `GoogleCalendarConnector.render` calls this when preparing the event title. This small guard keeps the rendered calendar note clean even if a record is missing a title or contains an unexpected value.

*Call graph*: called by 1 (render).


### Documents and Drive Files
Connectors that read Google Docs and broader Drive resources, including metadata and collaboration artifacts.

### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between Google Docs and the rest of the system’s source-sync pipeline. Its job is to ask Google Drive which Google Docs are visible to the current grant, fetch each document from the Google Docs API, and package the result in a consistent record shape.

It works in two stages. First, it uses the Google Drive file list because Drive knows which documents exist, when they changed, who owns them, and where their browser link is. It asks only for real Google Docs, skips trashed files, and can use a saved timestamp cursor so it only reads documents modified since the last sync. Then, for each file ID, it calls the Docs API to fetch the full document contents.

The connector is careful about partial failure. If Drive itself refuses access, the whole stream is skipped because the connector cannot know what documents exist. But if one listed document cannot be opened, it returns a small stub record for that one document instead of failing the whole run. This is like a librarian listing every book on a shelf, but writing “title known, pages unavailable” for a locked book.

Finally, the file includes rendering logic that walks Google Docs’ nested document structure and pulls out paragraph text in reading order, producing simple prose for indexing or display.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the Google Docs stream. It gathers document file entries from Drive, fetches each full document, combines the Drive metadata with the Docs content, and yields records in batches so the sync can process them steadily instead of all at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp from a previous sync. It asks `_iter_doc_files` for pages of Google Doc files, skips any malformed file entry without a usable ID, calls `_document` for each valid ID, then builds one flat record with document content, title, URL, creation time, update time, MIME type, and the original Drive file data. It outputs lists of records, up to a fixed page size, and raises a stream-skip signal if Google refuses the Drive or Docs access needed for the stream.

**Call relations**: During a sync, the broader connector framework calls this method to get Google Docs records. It relies on `_iter_doc_files` to discover which files exist and on `_document` to fetch each document’s full contents. If Google returns an access refusal for the overall stream, it turns that into `StreamSkipped` so the rest of the sync can continue without treating this source as a crash.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one Google Doc by its file ID from the Google Docs API. It exists to isolate the “open this document” step and to make unreadable or vanished documents safe for the larger sync.

**Data flow**: It receives an HTTP client and a Google file ID. It asks the Docs API for that document and returns the document data if the call succeeds. If Google says the document is forbidden or not found, it returns a minimal record containing only the document ID, so the caller still has something to represent that file; other errors are passed upward unchanged.

**Call relations**: `paginate` calls this after Drive has provided a file ID. The result goes back into `paginate`, which combines it with Drive metadata such as the file name, link, and modified time. This keeps one inaccessible document from stopping the entire Google Docs stream.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through Google Drive’s file list and finds Google Docs that should be synced. It is responsible for filtering to documents, ignoring trashed items, honoring the saved update cursor, and following Google’s pagination tokens.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for untrashed Google Docs, adds a “modified after this time” condition when a cursor is present, then repeatedly requests pages from Drive. For each response, it converts the `files` field into a safe list and yields it if it contains entries. It continues until Drive stops returning a next-page token.

**Call relations**: `paginate` calls this first, because it needs Drive’s list before it can fetch document bodies. This function hands batches of Drive file metadata back to `paginate`, which then uses each file ID to call `_document`.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one synced Google Docs record into a human-readable text block. The rest of the system can use that text for search, recall, or display without needing to understand Google Docs’ nested API format.

**Data flow**: It receives a document record and the stream description. It reads the title if present, asks `_plain_text` to extract the body text, builds a simple heading that identifies the source and stream, and returns both the title and the final rendered text. It does not change the record.

**Call relations**: The connector framework calls this when it needs prose from a synced record. `render` delegates the tricky document-body extraction to `_plain_text`, then wraps the result with a clear heading so the text carries context when indexed or shown later.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable paragraph text from the nested structure returned by the Google Docs API. It strips away the API’s container objects and keeps only the actual character content a person would read.

**Data flow**: It receives a document record. It looks for `body.content`, walks each content item, finds paragraph elements, then collects the text from each text run in order. It joins those pieces into one string, trims extra whitespace from the ends, and returns the plain text. If the expected structure is missing, it safely returns an empty string.

**Call relations**: `GoogleDocsConnector.render` calls this when preparing a document for recall or display. This helper does not contact Google or depend on the sync process; it simply translates a Google Docs-shaped record into prose.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `active during source sync runs`

This connector is the bridge between UFO and Google Drive. Its job is read-only: it does not create or edit Drive content. On the first run, it lists all non-trashed files the connected account can see, including files in shared drives. After that, it uses Google Drive’s change feed, which is like a “what changed since last time?” log, so later runs do not need to scan every file again.

The file defines the available streams: files, shared drives, permissions, comments, and revisions. A stream is one kind of data the sync system can pull. Files are the main stream and have a cursor, meaning a saved bookmark for where the previous sync stopped. Permissions, comments, and revisions are child data: the connector first finds files, then asks Google Drive for each file’s related child records.

It also knows how to deal with common Drive API realities. If a saved change token has expired, it raises a special “cursor expired” signal so the wider system can refresh from scratch. If the account does not have Drive permission, it marks the stream as skipped rather than treating the whole sync as broken. Finally, it can render a Drive file into a small readable text block containing its name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Google Drive syncing. Given a requested stream, it chooses the right way to fetch that stream’s pages from Google Drive.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor saved from an earlier run. If the stream is files, it either reads the change feed from the cursor or does a first full file listing and then saves a fresh change token. If the stream is shared drives, it lists drives. If the stream is permissions, comments, or revisions, it walks through files and fetches that child data. It yields plain record batches or StreamPage objects that can include records, deletes, and the next cursor. If Google refuses access because the grant lacks Drive scope, it changes that failure into a clean stream skip.

**Call relations**: The wider connector framework calls this when it wants records for a Google Drive stream. It hands the real work to _paginate_file_changes, _paginate_files, _start_page_token, _paginate_shared_drives, or _paginate_file_children depending on the stream name. When access is refused, it raises StreamSkipped so the sync run can record a skip instead of crashing.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists visible, non-trashed Google Drive files in pages. It is used for the initial file sync and also as the starting point for fetching per-file child data.

**Data flow**: It receives an HTTP client and an optional modified-time cursor. It builds a Google Drive files query, asks for up to 1000 files at a time, cleans the returned files field into a list, and yields each non-empty batch. It follows Google’s next-page token until there are no more pages.

**Call relations**: GoogleDriveConnector.paginate calls this during a first files sync. GoogleDriveConnector._paginate_file_children also calls it so it can discover every file before asking for that file’s permissions, comments, or revisions.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current change-feed bookmark. That bookmark lets the next sync ask, “what changed after this point?”

**Data flow**: It receives an HTTP client, calls Google Drive’s startPageToken endpoint, reads the startPageToken value from the response, and returns it if it is a usable non-empty string. Otherwise it returns nothing.

**Call relations**: GoogleDriveConnector.paginate calls this after completing the initial full file listing. The returned token becomes the next cursor for future file syncs.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads Google Drive’s change feed for files after a saved cursor. It is what makes later syncs efficient because it pulls only changed, removed, or trashed files instead of re-reading everything.

**Data flow**: It receives an HTTP client and a required change token cursor. For each change-feed page, it asks Google Drive for changes, separates live file records from deleted or trashed file IDs, and yields a StreamPage containing new or updated records, delete markers, and the next cursor. If Google says the token is too old, it raises CursorExpired so the system knows it must refetch fresh data.

**Call relations**: GoogleDriveConnector.paginate calls this when syncing the files stream with an existing cursor. It creates StreamPage results for the sync engine and raises CursorExpired when Google returns the specific expired-token response.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists the shared drives available to the connected account. Shared drives are re-read as a full list each run rather than tracked through the file change cursor.

**Data flow**: It receives an HTTP client, requests shared drive pages from Google Drive, turns the drives field into a safe list, and yields each non-empty batch. It follows the next-page token until Google has no more shared drives to return.

**Call relations**: GoogleDriveConnector.paginate calls this when the requested stream is shared_drives. It supplies the shared-drive record batches directly back to the sync flow.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches data that belongs to individual files, such as permissions, comments, or revisions. It works like visiting each folder label on a set of documents and then reading the attached notes for each document.

**Data flow**: It receives an HTTP client, a child stream description, and an optional cursor. First it lists all files with _paginate_files. For each file with a valid ID, it calls the matching Google Drive child endpoint, pages through the results, optionally filters child records by their cursor field, and yields records enriched with the parent file’s ID and name. If a specific file’s child data is forbidden or missing, it skips that file’s child collection and keeps going.

**Call relations**: GoogleDriveConnector.paginate calls this for permissions, comments, and revisions. This function depends on _paginate_files to discover parent files, then returns child record batches to the same pagination flow.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a small human-readable text summary. That text is useful when synced metadata needs to be recalled or displayed in a search-like context.

**Data flow**: It receives one record and the stream it came from. For non-file streams, it lets the base connector render them normally. For file records, it reads the name, MIME type, owners, and web link, builds a title and a short text body, and returns both. It uses _str to safely treat a missing or non-text name as an empty string.

**Call relations**: When rendering a files record, this function calls _str to clean the title before building the text. For all other streams, it hands off to the parent RestConnector rendering behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely converts a value into text only if it is already a string. It prevents unexpected non-text values from being used as a file title.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. If not, it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when it reads a file name from a Google Drive record, so the rendered title is always safe text.

*Call graph*: called by 1 (render).


### Meetings and Spreadsheets
Connectors that turn Google Meet artifacts and Google Sheets data into recallable text and structured records.

### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `source sync`

This connector is like a careful librarian for Google Meet. It asks Google’s Meet API for recent conference records, then checks each meeting for useful artifacts: transcripts and AI notes. A conference only becomes an output page if at least one of those artifacts exists.

The file is built around `GoogleMeetConnector`, which plugs into the system’s general REST connector framework. During a sync, it pages through Google Meet conference records, using a cursor so it can continue from the newest meeting it has already seen. It deliberately looks back one day because Google may create transcripts or smart notes after a meeting ends. That way, late-arriving notes are not missed.

For each conference, it fetches transcript sessions, transcript entries, smart-note records, and links to the backing Google Docs files. If the connector is allowed to read the Google Doc for smart notes, it pulls out the plain text and includes it. If Google refuses access or the document is gone, the connector keeps the document link instead of failing the whole run. Meet permission failures are treated as a skipped stream, not a broken sync.

Finally, the file renders each meeting as readable Markdown-style prose: meeting details, transcript dialogue grouped by speaker, and AI summaries.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for the Google Meet stream. It walks through Google Meet conference records page by page, collects only meetings that have transcripts or smart notes, and yields them to the rest of the system as sync pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It builds Google Meet API request parameters, including a one-day lookback filter when a cursor exists, fetches conference pages, enriches each conference through `_conference_record`, and emits `StreamPage` objects containing usable meeting records plus the next cursor. If Google returns a permission refusal, it changes that into a skipped stream message rather than a hard failure.

**Call relations**: The sync framework calls this when it wants records for the `meeting_artifacts` stream. Inside the loop it uses `_lookback` to avoid missing late-generated artifacts, `_max_start_time` to advance the cursor, `_conference_record` to gather the meeting details, and `list_or_empty` to safely treat missing API lists as empty lists.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds one complete internal record for a Google Meet conference. It gathers the meeting’s basic timing details plus any transcript and smart-note artifacts attached to it.

**Data flow**: It receives the raw conference object from Google. It reads the conference resource name, fetches transcript artifacts and smart-note artifacts under that conference, converts each artifact into a cleaner record, and returns one dictionary representing the meeting. The returned record includes the conference id, title, start and end times, expiration time, transcripts, and smart notes.

**Call relations**: `paginate` calls this for every conference returned by Google. This function then delegates the artifact listing to `_artifacts`, transcript shaping to `_transcript`, smart-note shaping to `_smart_note`, and small string/id cleanup to `_str` and `_resource_id`.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind under a conference, such as all transcript sessions or all smart-note sessions. It hides the API paging details from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the artifact collection name. If there is no parent name, it returns an empty list. Otherwise it repeatedly asks the Meet API for artifact pages, appends each page’s items to a list, follows any next-page token, and returns the full list.

**Call relations**: `_conference_record` calls this twice for each conference: once for transcripts and once for smart notes. It relies on `list_or_empty` so that absent or malformed list fields from Google do not crash the sync.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet transcript session into a clean record that the system can store and render. It also fetches the individual transcript lines spoken during the meeting.

**Data flow**: It receives the raw transcript object. It extracts the transcript name, id, state, start and end times, and Google Docs destination information, then calls `_transcript_entries` to retrieve the per-speaker text entries. It returns a dictionary containing all of that transcript data.

**Call relations**: `_conference_record` calls this for each transcript artifact found under a meeting. This function uses `_docs_destination` for the linked Google Doc, `_transcript_entries` for spoken lines, and `_resource_id` plus `_str` for safe cleanup.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual lines inside a transcript, including who spoke, what they said, and when. It gives the renderer enough detail to produce readable dialogue.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty, it returns no entries. Otherwise it pages through the transcript entries API, converts each raw entry into a simpler dictionary, and returns the list. If Google says the entries are inaccessible or missing, it returns whatever entries it already collected instead of stopping the whole run.

**Call relations**: `_transcript` calls this while building a transcript record. It uses `_str` and `_resource_id` to normalize entry names and `list_or_empty` to safely read Google’s entry list.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a clean record. When possible, it also pulls the actual note text from the linked Google Doc so the summary can be searched directly.

**Data flow**: It receives the raw smart-note object. It extracts the note id, name, state, timing fields, and Google Docs destination. If the record includes a document id, it asks `_document_text` for the document’s plain text and adds that text as `body` when available. It returns the finished smart-note dictionary.

**Call relations**: `_conference_record` calls this for each smart-note artifact. It uses `_docs_destination` to find the linked document, `_document_text` to read it, and `_resource_id` plus `_str` to make safe string fields.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads the plain text from a Google Docs document used by smart notes. It lets the connector include the summary text itself, not just a link to the document.

**Data flow**: It receives an HTTP client and a Google Docs document id. It URL-escapes the id so it is safe inside an API path, requests the document from the Google Docs API, and converts the structured document response into plain text through `_plain_text`. If the document is missing or access is denied, it returns an empty string.

**Call relations**: `_smart_note` calls this only when a smart note points to a Google Doc. This function hands the raw Docs API response to `_plain_text`, which strips away the document structure and keeps the readable text.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored meeting artifact record into readable text for indexing or display. It formats the conference details, transcripts, and AI summaries into a Markdown-like page.

**Data flow**: It receives one meeting record and its stream description. For the Google Meet meeting-artifacts stream, it reads the title and main fields, builds labeled meeting metadata, adds a transcript section, adds a smart-notes section, and returns the page title plus the full text body. For any other stream, it falls back to the parent connector’s rendering behavior.

**Call relations**: The broader source system calls this after records have been fetched and need to become recallable prose. It uses `_labeled` for compact metadata blocks, `_transcripts_section` for transcript text, `_smart_notes_section` for summaries, and `_str` to avoid non-string surprises.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcripts for a meeting into a readable section. It includes transcript metadata and the spoken dialogue.

**Data flow**: It receives a value that should contain transcript records. It safely treats missing data as an empty list, returns an empty string when there are no transcripts, and otherwise builds a `Transcripts` section. For each transcript it formats state, timing, document link, and dialogue text, then joins the pieces into one string.

**Call relations**: `GoogleMeetConnector.render` calls this while building the final meeting page. It uses `_labeled` for transcript metadata, `_dialogue` for speaker lines, `_str` for safe text conversion, and `list_or_empty` for defensive list handling.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats all AI-generated meeting summaries into a readable section. It shows note metadata, a document link, and the note body when the connector was able to read it.

**Data flow**: It receives a value that should contain smart-note records. It safely turns missing data into an empty list, returns an empty string when there are no notes, and otherwise builds an `AI summaries` section. For each note it formats state, timing, document link, and any available body text, then joins everything into one string.

**Call relations**: `GoogleMeetConnector.render` calls this when assembling the final page. It uses `_labeled` to present metadata, `_str` to clean text fields, and `list_or_empty` to avoid breaking on absent lists.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into readable conversation lines. It also joins back-to-back entries from the same speaker so the transcript reads more naturally.

**Data flow**: It receives transcript entries. It skips empty text, finds a readable speaker name for each entry, and builds lines such as `Speaker: text`. If the same speaker continues on the next entry, it appends the new text to the previous line. It returns the dialogue as newline-separated text.

**Call relations**: `_transcripts_section` calls this when it needs the spoken content for a transcript. It uses `_speaker` to name each participant, `_str` to safely read text, and `list_or_empty` to handle missing entry lists.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the structured response returned by the Google Docs API. Google Docs returns documents as nested objects, and this function pulls out only the text runs.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the document body, walks through paragraph content, collects each text fragment, joins all fragments together, trims extra outer whitespace, and returns the plain text string.

**Call relations**: `_document_text` calls this after successfully fetching a smart-note Google Doc. It is the final conversion step from Google’s document structure into text the rest of the connector can store and render.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This extracts the Google Docs destination attached to a transcript or smart note. That destination gives both the document id and an export link users can follow.

**Data flow**: It receives a raw artifact record. If the record has no valid `docsDestination` object, it returns an empty dictionary. Otherwise it returns a small dictionary with `docs_document` and `docs_url`, converted safely to strings.

**Call relations**: `_transcript` and `_smart_note` call this while shaping artifact records. It uses `_str` so missing or non-string fields become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This finds the newest conference start time seen in a batch. It is used to move the sync cursor forward.

**Data flow**: It receives a list of conference records and the current cursor. It checks each conference’s `startTime`, keeps the greatest string timestamp it sees, and returns that value. If there is nothing newer, it returns the original cursor.

**Call relations**: `paginate` calls this after fetching a page of conferences. The returned timestamp becomes the next cursor in the emitted `StreamPage`, which tells future syncs where to resume.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This shifts a cursor timestamp backward by one day. It helps the connector refetch recent meetings because Google may create transcripts or notes after the meeting has ended.

**Data flow**: It receives a timestamp string, parses it as a date and time, subtracts the configured one-day lookback period, and returns the adjusted timestamp in the format Google’s API expects.

**Call relations**: `paginate` calls this when it has an existing cursor and needs to build a Meet API filter. This is the connector’s safety net for late-arriving artifacts.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: This pulls the short id from a longer Google resource name. For example, it keeps the part after the final slash.

**Data flow**: It receives a resource name string. If the string is present, it splits from the right on `/` and returns the final piece; otherwise it returns an empty string.

**Call relations**: Several record-building functions call this when they need stable, compact ids for conferences, transcripts, smart notes, and transcript entries. `_speaker` also uses it to turn participant resource names into readable speaker labels.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses a readable speaker label for a transcript entry. If Google provides a participant resource name, it uses the short id; otherwise it falls back to `Participant`.

**Data flow**: It receives the participant value from a transcript entry. It safely converts it to a string, extracts the short resource id, and returns that id or the default label `Participant`.

**Call relations**: `_dialogue` calls this for each transcript entry while building speaker-prefixed lines. It relies on `_str` and `_resource_id` for safe cleanup.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only if it is already a string. It prevents accidental non-string values from leaking into rendered text fields.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Many functions use this while reading Google API responses, because external APIs may omit fields or return unexpected shapes. It supports record building, rendering, document-link extraction, dialogue formatting, and speaker naming.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats simple label-and-value pairs as readable lines. It is used for compact metadata blocks such as `start: ...` and `doc: ...`.

**Data flow**: It receives a list of label/value pairs. It keeps only pairs with a non-empty value, turns each into `label: value`, joins them with newlines, and returns the resulting text.

**Call relations**: `GoogleMeetConnector.render`, `_transcripts_section`, and `_smart_notes_section` call this when building the final Markdown-like page. It provides a shared way to show metadata without cluttering the output with empty fields.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `during source sync`

This connector is a read-only bridge between UFO and Google Sheets. Its job is to turn a user's Google spreadsheets into content the rest of the system can sync and search later. Without it, the system would not know how to discover spreadsheets, list their sheet tabs, or fetch the rows inside each tab.

The file talks to two Google services. First it asks Google Drive for spreadsheet files, because Drive is where Google lists documents and their modified times. Then, for each spreadsheet file, it asks the Google Sheets API for the spreadsheet details, such as the title and tab list. Think of Drive as the library catalog and Sheets as the book itself.

It exposes three streams of information: whole spreadsheets, individual sheet tabs, and the grid values inside each tab. The spreadsheet stream is incremental, meaning it can ask Google only for files changed after the last saved update time instead of rereading everything every time.

The connector is careful about permissions. If the connected account lacks the needed Google access scope, it marks the stream as skipped rather than crashing the whole sync. If Drive can see a spreadsheet but Sheets cannot open it, it keeps the basic Drive metadata instead of throwing everything away. Finally, the render method turns raw records into simple text bodies that can be stored and recalled.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 54–84)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main paging routine for the connector. It decides which Google Sheets stream is being requested, gathers the right kind of records, and yields them in batches so the sync system can process them steadily instead of all at once.

**Data flow**: It receives an HTTP client, a stream description, and possibly a saved cursor time. It reads spreadsheet records first, then either keeps them as spreadsheet records, expands them into tab records, or fetches cell-value records for each tab. It outputs lists of records, up to the configured page size, and turns certain Google permission errors into a clean “stream skipped” result.

**Call relations**: The sync runner calls this when it wants data from one of the Google Sheets streams. This function then calls _spreadsheet_records as the shared starting point, uses _sheet_records when the stream needs tab-level records, and uses _sheet_value_records when the stream needs row data. If Google refuses access with an authorization-style error, it raises StreamSkipped so the larger run records a skip rather than treating the connector as broken.

*Call graph*: calls 4 internal fn (__init__, _sheet_value_records, _spreadsheet_records, _sheet_records).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 86–111)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks Google Drive for spreadsheet files the account can access. It is responsible for walking through Drive's paged results and applying the “changed after this time” filter when an incremental sync is possible.

**Data flow**: It receives an HTTP client and an optional cursor time. It builds a Drive search query for non-trashed Google Sheets files, adds the cursor filter if present, repeatedly requests pages from Drive, cleans the returned file list with list_or_empty, and yields each non-empty page of file metadata. It stops when Drive no longer provides another page token.

**Call relations**: _spreadsheet_records relies on this function as its file finder. In the bigger flow, paginate asks for spreadsheet records, and those records begin here as Drive file entries before being enriched with Sheets metadata.

*Call graph*: called by 1 (_spreadsheet_records); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_records`  (lines 113–143)

```
async def _spreadsheet_records(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function turns Drive file entries into full spreadsheet records. It adds Sheets API metadata such as spreadsheet title, URL, and tab list, while preserving Drive timestamps used for incremental syncing.

**Data flow**: It receives an HTTP client and an optional cursor. It gets spreadsheet files from _iter_spreadsheet_files, ignores entries without a usable ID, asks the Sheets API for each spreadsheet's metadata, and combines that data with Drive fields like creation time and modified time. If Sheets cannot open a spreadsheet because of access or missing-resource errors, it falls back to a minimal record built from Drive metadata.

**Call relations**: paginate calls this as the foundation for all three streams. For the spreadsheet stream, its output is used directly. For the tab and value streams, its output is passed onward so other helpers can break a spreadsheet into tabs or read each tab's rows.

*Call graph*: calls 1 internal fn (_iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 145–167)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function reads the actual cell rows from each tab in a spreadsheet. It creates one record per tab containing the tab's values plus enough context to know which spreadsheet and sheet the rows came from.

**Data flow**: It receives an HTTP client and one spreadsheet record. It looks through that spreadsheet's tab list, extracts each tab title and sheet ID, safely encodes the tab title for use in a web address, then requests the tab's values from the Sheets API. It yields records that include the raw row values, the spreadsheet ID and title, the sheet ID, and the sheet title.

**Call relations**: paginate calls this only for the sheet_values stream. It depends on _spreadsheet_records having already supplied a spreadsheet record with its tab metadata, and its output later becomes readable text through render when the system stores or displays synced content.

*Call graph*: called by 1 (paginate); 1 external calls (quote).


##### `GoogleSheetsConnector.render`  (lines 169–188)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a raw Google Sheets record into plain text. The rest of the system can then store or recall the content in a human-readable form rather than showing unprocessed API data.

**Data flow**: It receives one record and the stream it belongs to. For spreadsheet records, it builds text that names the spreadsheet and its tabs. For sheet-tab records, it names the tab and the parent spreadsheet. For value records, it converts the grid rows into lines of text. It returns a title and a formatted text body.

**Call relations**: The source framework calls render after records have been fetched and need to become recallable content. Inside this function, _str safely normalizes optional text fields, and _grid_text converts sheet rows into a readable table-like body. Unknown stream names are handed back to the parent connector's rendering behavior.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_sheet_records`  (lines 191–210)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper splits one spreadsheet record into separate records for each sheet tab. It lets the system treat tabs as their own items, not just as hidden parts of a spreadsheet.

**Data flow**: It receives a spreadsheet record. It reads the spreadsheet ID, loops through the spreadsheet's tab list, skips malformed tab entries, and builds a clean record for each tab with a stable ID, parent spreadsheet information, and tab title. It returns the list of tab records.

**Call relations**: paginate uses this helper when serving the sheets stream. The spreadsheet record comes from _spreadsheet_records, and this helper reshapes it into the tab-level view that downstream sync code expects.

*Call graph*: called by 1 (paginate).


##### `_grid_text`  (lines 213–218)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This helper converts raw spreadsheet rows into readable text. It makes cell values easier to store and search by placing cells on the same row side by side with separators.

**Data flow**: It receives a value that may or may not be a list of rows. If the value is not a list, it returns an empty string. Otherwise, it keeps row entries that are lists, converts each cell to text, joins cells with ` | `, and joins rows with line breaks.

**Call relations**: render calls this when it is formatting the sheet_values stream. It is the small formatting step that changes API-style grid data into a simple text body.

*Call graph*: called by 1 (render).


##### `_str`  (lines 221–222)

```
def _str(value: Any) -> str
```

**Purpose**: This helper safely turns optional or uncertain values into strings for display. It avoids accidentally rendering numbers, dictionaries, or missing values as misleading titles.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: render calls this whenever it needs a clean title or label from a record. It keeps the rendered text tidy even when Google data is incomplete or shaped differently than expected.

*Call graph*: called by 1 (render).

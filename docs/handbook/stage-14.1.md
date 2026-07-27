# Google Workspace source connectors  `stage-14.1`

This stage is the set of Google Workspace “source connectors.” A connector is a small adapter that talks to an outside service and reshapes its data into the system’s own searchable pages. It is part of the main syncing work: after an account is connected, these files fetch Google records, notice what changed, and pass clean text and metadata to the rest of the system.

The Gmail connector reads mailbox messages, turns them into plain searchable text, and uses change tracking so later syncs only fetch new or deleted mail. Google Calendar does the same for primary-calendar events and attendee lists. Google Docs reads accessible documents and extracts their text without ever editing them. Google Drive is the broad file cabinet connector: it streams files, shared drives, permissions, comments, revisions, and deletions from Google’s paged API results. Google Meet focuses on meeting artifacts, such as transcripts and Gemini notes, and turns them into readable pages. Google Sheets finds spreadsheets through Drive, splits them into tabs, and can read the rows inside each tab.

## Files in this stage

### Mail and scheduling
Connectors that sync personal communication records and their incremental changes from Gmail and Google Calendar.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync and record rendering`

Gmail does not return an email as one simple block of text. A message is a nested MIME tree, which means the readable parts may be buried in plain-text or HTML sections, and those sections are base64-encoded, a way of packaging bytes as text for transport. This file hides that complexity from the rest of the system.

The main class, GmailConnector, talks to the Gmail API. On the first run, it lists every message ID and then fetches each message body. On later runs, it uses Gmail’s historyId, which is like a bookmark in Gmail’s change log, to ask only what was added or deleted since the last run. If Gmail says that bookmark is too old, the connector tells the core system to start over from a full sync.

After fetching a message, the file flattens Gmail’s nested response into a simpler record: sender, recipients, subject, labels, and decoded body text. When the system needs text for recall or search, render builds something that looks like an email a person would read: From, To, Cc, Subject, then the body. If there is only HTML, a small HTML parser strips tags and keeps readable text. Without this file, Gmail messages would either not sync, or would be stored as hard-to-read API-shaped data instead of useful prose.

#### Function details

##### `GmailConnector.paginate`  (lines 75–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the connector’s main sync loop for Gmail messages. It decides whether to do a first-time full read or a later change-only read, fetches message bodies in batches, and yields pages of records and deletions back to the core sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the cursor is missing, it asks for all message IDs; if the cursor exists, it asks Gmail for changes since that point. It then fetches full bodies for added messages, groups them into StreamPage objects, attaches deletions and the next cursor on the final page, and turns permission failures into a clean skipped-stream result.

**Call relations**: The core source runner calls this when it wants Gmail data. It hands first-run work to GmailConnector._backfill, later-run work to GmailConnector._history, body loading to GmailConnector._fetch_bodies, and packages the result as StreamPage objects for the rest of the sync pipeline.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 109–126)

```
async def _backfill(self, client: httpx.AsyncClient) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first full mailbox scan. It lists every Gmail message ID so the connector can fetch all messages during an initial sync.

**Data flow**: It starts with an empty list and repeatedly asks Gmail’s messages list endpoint for pages of IDs. Each valid message ID is added to the list. When there are no more pages, it also asks GmailConnector._seed_history_id for a starting history bookmark, then returns both the IDs and that bookmark.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor. After gathering IDs, it passes control to GmailConnector._seed_history_id so future runs can switch from full scan to change tracking.

*Call graph*: calls 1 internal fn (_seed_history_id); called by 1 (paginate).


##### `GmailConnector._seed_history_id`  (lines 128–140)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str]) -> str | None
```

**Purpose**: This finds the Gmail history bookmark to save after a full backfill. That bookmark lets the next sync ask only for changes that happened later.

**Data flow**: It receives the list of message IDs from the backfill. If the list is empty, it returns no bookmark. Otherwise it fetches the newest listed message in minimal form, reads its historyId if present, and returns it; if that message disappeared meanwhile, it quietly returns no bookmark.

**Call relations**: GmailConnector._backfill calls this at the end of the first full mailbox listing. Its result is passed back up to GmailConnector.paginate as the next cursor for future incremental runs.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 142–177)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log from a saved historyId. It finds which messages were added and which were deleted since the previous sync.

**Data flow**: It receives an HTTP client and an old history ID. It asks Gmail’s history endpoint page by page for message-added and message-deleted events, extracts message IDs from those events, updates the latest history ID seen, and returns net-added IDs, deleted IDs, and the new cursor. If Gmail says the old history ID is no longer valid, it raises CursorExpired so the system can do a fresh full sync.

**Call relations**: GmailConnector.paginate calls this when a cursor exists. This function uses _message_ids to pull IDs out of Gmail’s event objects, and it signals CursorExpired when the caller must abandon incremental sync and refetch everything.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 179–193)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns a list of Gmail message IDs into full, simplified message records. It fetches each message body and skips messages that vanished between listing and fetching.

**Data flow**: It receives an HTTP client and message IDs. For each ID, it requests the full Gmail message; if Gmail returns 404, that one message is ignored, but other errors still stop the sync. Each successful raw message is passed to _flatten_message, and the finished list of records is returned.

**Call relations**: GmailConnector.paginate calls this after it knows which messages are newly added. This function hands each raw Gmail API response to _flatten_message so later stages receive clean records instead of nested Gmail payloads.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 195–214)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a stored Gmail record into readable text for recall, search, or display. It makes the synced email look like something a person would recognize, not like a raw API response.

**Data flow**: It receives a flat message record and stream description. For the Gmail messages stream, it reads the subject, sender, recipients, and body fields, formats a small header block, chooses the best body text, and returns a title plus the final prose. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The wider source system calls this when it needs text from a synced record. It relies on _str, _format_contact, _format_recipients, and _message_body to cleanly prepare each part of the final email-shaped text.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 217–226)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This pulls message IDs out of Gmail history event entries. Gmail wraps each ID inside a small nested object, so this helper extracts just the useful strings.

**Data flow**: It receives an unknown value that should be a list of event entries. It ignores anything that is not shaped like a Gmail message event, collects non-empty message ID strings, and returns them as a list.

**Call relations**: GmailConnector._history calls this while reading added and deleted events from Gmail’s change log. It keeps the history code focused on the sync flow rather than on repeated nested dictionary checks.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 229–256)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This reshapes a raw Gmail message into the simpler record format used by the sync system. It lifts out the useful headers, decoded bodies, labels, sender, recipients, and direction.

**Data flow**: It receives the full Gmail API message. It reads selected headers from the payload, extracts plain-text and HTML body content, parses addresses, gathers labels, decides whether the message is inbound or outbound, and returns one flat dictionary with stable field names.

**Call relations**: GmailConnector._fetch_bodies calls this for every fetched message. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 259–273)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches Gmail’s nested MIME payload for readable body parts. It returns the first plain-text body and the first HTML body it can find.

**Data flow**: It receives the message payload tree. It walks through the payload and its child parts, looking for text/plain and text/html sections with encoded body data. Matching data is decoded and saved, then the function returns a pair: plain text if found, and HTML if found.

**Call relations**: _flatten_message calls this while building a clean record. Its inner walk function does the recursive tree search and uses _b64url_decode when it finds encoded Gmail body content.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 263–270)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive tree-walker inside _extract_bodies. It visits one MIME part, checks whether it contains useful body text, and then visits its children.

**Data flow**: It receives one payload part. If that part is a plain-text or HTML body and has encoded data, it decodes the data and stores it if that body type has not already been found. Then it repeats the same process for every child part.

**Call relations**: _extract_bodies starts this helper on the top-level payload. Whenever the helper finds encoded body data, it hands decoding to _b64url_decode.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 276–282)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body text format into normal text. Gmail uses URL-safe base64 and may omit padding characters, so this helper fixes that before decoding.

**Data flow**: It receives an encoded string from Gmail. It adds any missing padding, decodes the URL-safe base64 bytes, converts them to UTF-8 text, and returns the result. If the data cannot be decoded, it returns an empty string instead of crashing.

**Call relations**: _extract_bodies.walk calls this when it finds a text/plain or text/html MIME body. It uses Python’s base64.urlsafe_b64decode to do the actual decoding.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 285–292)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This reads the first email address from a header such as From. It separates the address itself from the optional display name.

**Data flow**: It receives a header string or nothing. If there is no header or no parsed address, it returns two empty values. Otherwise it lowercases the email address, keeps the display name if present, and returns both.

**Call relations**: _flatten_message calls this for the sender header. It uses email.utils.getaddresses, a standard parser that understands common email address formats.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 295–302)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This reads all email addresses from a recipient header such as To or Cc. It turns one header string into a list of small recipient objects.

**Data flow**: It receives a header string or nothing. With no header, it returns an empty list. Otherwise it parses the addresses, lowercases each email address, keeps any display name, and returns a list of dictionaries with handle and display_name fields.

**Call relations**: _flatten_message calls this for the To and Cc headers. It uses email.utils.getaddresses so the rest of the file does not need to understand all the ways email addresses can be written.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 305–310)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable string. If there is a display name, it formats it like “Name <email@example.com>”; otherwise it uses just the email address.

**Data flow**: It receives a possible email handle and display name. If the handle is missing or not text, it returns an empty string. If the handle is valid, it combines it with the display name when available and returns the formatted contact.

**Call relations**: GmailConnector.render uses this for the sender, and _format_recipients uses it for each recipient. It is the small formatting rule that keeps contact display consistent.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 313–320)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient records into one comma-separated line for an email header. It is used for the To and Cc lines in rendered text.

**Data flow**: It receives a value that should be a list of recipient dictionaries. If it is not a list, it returns an empty string. For each dictionary, it formats the contact and joins all formatted contacts with commas.

**Call relations**: GmailConnector.render calls this when building the readable header block. It uses _format_contact for the actual per-person formatting.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 323–331)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for a message. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail’s snippet if no body was available.

**Data flow**: It receives a flat message record. It first checks body_text and returns it if non-empty. If not, it checks body_html and runs it through _HtmlText.extract to remove tags. If neither body exists, it returns the snippet text or an empty string.

**Call relations**: GmailConnector.render calls this when assembling the final email prose. It is the decision point that keeps rendered messages readable even when Gmail only supplies HTML.

*Call graph*: called by 1 (render).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only when it already is one. It avoids accidentally rendering non-text values as confusing output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this for the subject before using it in the rendered title and header.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 342–344)

```
def __init__(self) -> None
```

**Purpose**: This sets up the small HTML-to-text parser used for email bodies. It prepares a place to collect readable text pieces while the parser scans the HTML.

**Data flow**: It receives no external data beyond the new parser object being created. It initializes the parent HTML parser with automatic character-reference conversion, then creates an empty list for collected text and line breaks.

**Call relations**: _HtmlText.extract creates an instance of this parser before feeding it raw HTML. The parser’s later callback methods add content into the list prepared here.


##### `_HtmlText.extract`  (lines 347–352)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It strips tags, keeps the words, and preserves useful paragraph-like line breaks.

**Data flow**: It receives a raw HTML string. It creates a parser, feeds in the HTML, joins the collected text pieces, collapses extra whitespace on each line, removes empty lines, and returns the cleaned text.

**Call relations**: _message_body uses this when a message has no plain-text body but does have HTML. During parsing, the HTMLParser framework calls _HtmlText.handle_data, _HtmlText.handle_starttag, and _HtmlText.handle_endtag as it sees content and tags.


##### `_HtmlText.handle_data`  (lines 354–355)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records the actual words found inside an HTML body. It is called whenever the parser sees text between tags.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that chunk to the parser’s collected parts, which will later be joined and cleaned by _HtmlText.extract.

**Call relations**: The HTMLParser base class calls this while _HtmlText.extract feeds it HTML. It contributes the readable content that remains after tags are removed.


##### `_HtmlText.handle_starttag`  (lines 357–359)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag usually means a new visual block, such as a paragraph or list item. This helps the plain-text result keep a readable shape.

**Data flow**: It receives an HTML tag name and its attributes. If the tag is one of the known block-style tags, it appends a newline marker to the collected parts; otherwise it ignores the tag.

**Call relations**: The HTMLParser base class calls this during _HtmlText.extract. It works with _HtmlText.handle_endtag so cleaned HTML does not become one long run-on line.


##### `_HtmlText.handle_endtag`  (lines 361–363)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a closing HTML tag marks the end of a visual block. It helps preserve paragraph and table-like separation in the final text.

**Data flow**: It receives an HTML tag name. If the tag is a known block-style tag, it appends a newline marker to the collected parts; otherwise it does nothing.

**Call relations**: The HTMLParser base class calls this during _HtmlText.extract. Together with _HtmlText.handle_starttag and _HtmlText.handle_data, it produces the text that extract later cleans and returns.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the rest of the system. Without it, calendar meetings would not become recallable content, and the system would not know who was invited to each event.

It defines two streams, which are like two views of the same Google data. The first stream, `calendar_events`, stores one record per calendar event. The second, `event_attendees`, stores one record per invited person per event. That second view makes attendee information easier to query later, much like keeping both a meeting agenda and a separate sign-in sheet.

The connector reads from Google’s events API. On the first run, it looks back 90 days and asks Google for events, including deleted ones. Google returns a `syncToken`, which is a bookmark for the next run. Later runs send that bookmark back to Google and receive only changed or cancelled events. Cancelled events become delete markers for the main event stream.

If Google says the bookmark has expired, the connector raises `CursorExpired` so the larger sync system can start fresh. If the user’s permission grant does not include Calendar access, it raises `StreamSkipped` instead of treating that as a broken run. The file also renders event records into readable text with title, time, location, attendees, and description.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches pages of Google Calendar event changes and turns them into sync pages for the rest of the system. It supports both the event stream and the per-attendee stream.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. If a cursor is present, it asks Google for changes since that cursor; otherwise it asks for events from the last 90 days and includes deleted events. It reads each Google event, skips malformed items, turns active events into records, turns cancelled events into delete IDs for the event stream, and yields `StreamPage` objects. At the end, it passes along Google's next sync token as the new cursor.

**Call relations**: The sync engine calls this when it needs records from Google Calendar. Inside the loop it hands raw event data to `_flatten_event` for normal event records or `_flatten_attendees` for attendee rows. If Google reports an expired sync token, it raises `CursorExpired`; if Calendar permission is missing, it raises `StreamSkipped` so the wider run records a skip rather than a failure.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced calendar event record into a human-readable title and body. This is what makes an event understandable when it is later searched or recalled.

**Data flow**: It receives a record and its stream definition. For `calendar_events`, it reads the title, start and end time, location, attendee handles, and description, then builds a plain text summary. For other streams, it falls back to the base connector's rendering behavior.

**Call relations**: The larger source framework calls this when it needs display or indexable text for a record. It uses `_str` to safely treat a missing or non-text title as an empty string, then returns the finished title and body.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the simpler event shape used by this system. It keeps the important meeting details and folds attendee handles into the event record.

**Data flow**: It receives a raw event dictionary from Google. It reads fields such as ID, creation and update times, summary, description, location, start and end time, organizer, recurrence IDs, and attendees. It normalizes times through `_parse_when`, normalizes organizer and attendee email addresses to lowercase, and returns one flat dictionary ready to sync.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for each active event in the `calendar_events` stream. While building the event, it calls `_attendee` for each valid attendee and `_parse_when` for start and end times.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Creates a small, consistent attendee summary from Google's attendee data. It keeps the attendee's email handle, display name, and response status.

**Data flow**: It receives one attendee dictionary. It lowercases the attendee email address, copies the display name if present, translates Google's response value into this system's response wording, and returns a compact attendee dictionary.

**Call relations**: `_flatten_event` calls this while building the attendee list embedded inside a calendar event record. It does not fetch anything itself; it only reshapes one attendee object.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Splits one Google Calendar event into separate rows, one for each attendee. This lets the system store invitees as their own records tied back to the event.

**Data flow**: It receives a raw event dictionary. It reads the event ID, organizer email, event timestamps, and attendee list. For each attendee with an email address, it builds a record ID from the event ID and attendee handle, records the attendee role, response, display name, and whether the attendee is the current user, then returns the full list of attendee rows.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for active events when syncing the `event_attendees` stream. It calls `_attendee_role` to decide whether each attendee is an organizer, resource, optional attendee, or required attendee.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in a calendar event. This turns several Google flags into one simple role value.

**Data flow**: It receives an attendee dictionary and a separate flag saying whether that attendee matches the organizer email. It checks, in order, whether the person is the organizer, a booked resource, optional, or otherwise required. It returns one role string.

**Call relations**: `_flatten_attendees` calls this while creating one row per attendee. The role it returns becomes part of the attendee record that is synced downstream.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar start or end time data into one timestamp-like string. It supports both timed events and all-day events.

**Data flow**: It receives a value that may be a Google time object. If it contains `dateTime`, it returns that value as text. If it contains an all-day `date`, it turns it into midnight UTC on that date. If the input is not a usable time object, it returns nothing.

**Call relations**: `_flatten_event` calls this for an event's start and end fields. This keeps the rest of the synced event record from needing to know Google's two different time formats.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a possible value into text only if it is already a string. It prevents non-text values from leaking into rendered event titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `GoogleCalendarConnector.render` calls this when preparing the event title. It is a small guardrail around data that may be missing or not shaped as expected.

*Call graph*: called by 1 (render).


### Drive documents
Connectors that read Google Docs and broader Google Drive file metadata, permissions, comments, revisions, and deletion state.

### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `source sync`

This connector solves a common problem: Google Docs are not stored as simple text files, and listing them is separate from reading their contents. The file first asks Google Drive for the list of Google Docs the account can access. It only asks for real Google Docs, skips trashed files, and can resume from a saved “modified time” so later syncs only fetch changed documents.

For each Drive file it finds, it then asks the Google Docs API for the full document. If one document cannot be opened, for example because permissions changed or it disappeared, the connector does not stop the whole sync. It keeps a small placeholder record using the file id, like writing down a book title even if the shelf is locked. But if Drive itself refuses the listing request, the stream is skipped because the connector cannot discover what to sync.

The fetched document is wrapped with useful Drive information such as title, link, creation time, update time, and owner data. Later, the render step turns Google Docs’ nested document structure into readable prose by walking paragraph text runs in order. That text becomes the searchable body the rest of the system can use.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Google Docs in batches for the sync process. It combines Drive file metadata with the full Docs API document, and yields pages of records so the system can process many documents without holding everything forever.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor that marks the last update time already synced. It asks _iter_doc_files for Drive file pages, fetches each document body through _document, adds standard fields such as document id, title, URL, created time, updated time, and the original Drive file data, then outputs lists of records. If Drive or Docs refuses access at the stream level, it turns that into a StreamSkipped message instead of a crash.

**Call relations**: This is the main reading loop for the connector. During a sync, it relies on _iter_doc_files to discover candidate Google Docs, then calls _document for each file id to fetch the actual document content. If access is refused broadly, it creates a StreamSkipped error so the wider sync system can record that this source could not be read.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: Fetches one full Google Doc from the Google Docs API. It protects the sync from failing just because a single listed document cannot be opened.

**Data flow**: It receives an HTTP client and a Google Drive file id. It requests the document from the Docs API and returns the document data. If Google says the document is forbidden or missing, it returns a small stub containing only the document id; for other errors, it lets the error continue upward.

**Call relations**: paginate calls this after Drive has supplied a file id. Its result is merged with Drive metadata into the final record. This keeps the main sync moving even when one document has stale permissions or was deleted after Drive listed it.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Lists Google Docs from Google Drive, one Drive page at a time. It uses the saved cursor to ask Google only for documents modified after the last successful sync.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for non-trashed Google Docs, adds a modified-time filter if a cursor exists, and repeatedly requests Drive pages using Google’s next-page token. Each response’s files value is normalized with list_or_empty, then yielded as a list; when there is no next-page token, it stops.

**Call relations**: paginate calls this first to discover which files should be fetched. This function hands back Drive file metadata, and paginate then uses each file id to call _document for the richer Docs API content.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one synced Google Docs record into a title and readable text block. This is what makes the document useful as prose instead of raw Google API data.

**Data flow**: It receives a record and the stream description. It reads the title if present, asks _plain_text to extract the document body text, builds a heading that includes the connector and stream name, and returns both the title and the final formatted text.

**Call relations**: After records have been fetched by the connector, the wider system can call render to prepare them for recall or indexing. render delegates the Google Docs body traversal to _plain_text, then wraps the result in a consistent heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: Extracts the visible paragraph text from a Google Docs API document structure. It turns Google’s nested body format into one plain string.

**Data flow**: It receives a document record. It looks inside body.content, finds paragraph elements, then collects each textRun content value in order. It joins all those pieces together, trims extra whitespace at the ends, and returns the resulting plain text. Non-paragraph items, missing fields, and unexpected shapes are quietly skipped.

**Call relations**: GoogleDocsConnector.render calls this when it needs the readable body of a document. It does the low-level unpacking so render can focus on producing the final title and formatted text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `source sync runs`

This connector is the bridge between the project and Google Drive. Without it, the system would not know how to ask Google Drive for files, notice later changes, or collect useful side information such as comments and permissions.

The main job is to read, not write. It uses Google Drive’s web API through an HTTP client, but it does not hold the access token itself; the runner supplies credentials through the project’s authentication layer. The connector defines several streams: files, shared drives, permissions, comments, and revisions. A stream is simply one kind of data the sync engine can pull.

The file stream is special. On the first run, it lists every non-trashed file and then saves a Google “start page token,” which is like a bookmark saying, “next time, start watching changes from here.” On later runs, it uses that bookmark to fetch only changes. If Google says the bookmark is too old, the connector reports that the cursor expired so the wider system can do a fresh sync.

Shared drives are re-read fully each time. Permissions, comments, and revisions are gathered by first listing files, then visiting each file’s child collection. If Google refuses access because the account lacks Drive permission, the stream is marked skipped rather than failed.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main doorway the sync engine uses to ask for Google Drive data. It chooses the right fetching method for the requested stream, such as files, shared drives, permissions, comments, or revisions.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It checks the stream name, calls the matching helper, and yields pages of records or cursor updates back to the sync engine. If Google rejects the request with an authorization-related status, it turns that into a skipped stream instead of a hard failure.

**Call relations**: When the wider connector framework asks for pages, this function dispatches the work. For file streams it calls either _paginate_file_changes or _paginate_files, then _start_page_token after the first full file scan. For shared drives it calls _paginate_shared_drives. For permissions, comments, and revisions it calls _paginate_file_children. It creates StreamPage objects when it needs to pass back cursor-only or change-tracking information, and it raises StreamSkipped when Drive access is missing.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists Google Drive files in pages. It is used for the first full file sync and also as the file list that child streams use before fetching per-file data.

**Data flow**: It starts with an HTTP client and an optional timestamp cursor. It builds a Google Drive search for non-trashed files, optionally newer than the cursor, asks Google for one page at a time, converts the returned file list into a safe list, and yields each non-empty group of file records. It keeps following Google’s next-page token until there are no more pages.

**Call relations**: paginate calls this during an initial files run. _paginate_file_children also calls it so it can discover which files need permissions, comments, or revisions fetched. It relies on list_or_empty to treat missing or malformed list data as an empty list rather than breaking the paging loop.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the bookmark used to track future changes. The bookmark lets later syncs avoid rereading every file from scratch.

**Data flow**: It sends a request for Google Drive’s changes start token. If the response contains a usable string token, it returns that token; otherwise it returns nothing.

**Call relations**: paginate calls this after the first full file listing is complete. The token it returns is wrapped in a StreamPage so the sync engine can save it as the next cursor for future file-change runs.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This function reads Google Drive’s change feed after the initial sync. It finds files that changed, files that were removed, and files that were moved to the trash.

**Data flow**: It receives an HTTP client and a saved change token. For each page of Google changes, it separates live file records from deleted or trashed file IDs. It yields StreamPage objects containing records to upsert, deletes to tombstone, and the next cursor to save. If Google says the token is expired, it raises CursorExpired so the system knows it must resync from scratch.

**Call relations**: paginate calls this whenever the files stream already has a cursor. Inside the loop it uses list_or_empty to safely read the changes list and creates StreamPage objects to hand both updates and deletions back to the sync engine. When Google returns the specific expired-token response, it creates CursorExpired rather than hiding the problem.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists the shared drives visible to the connected Google account. Shared drives are reread as a complete set each sync rather than tracked through the file-change cursor.

**Data flow**: It starts with an HTTP client, requests shared-drive pages from Google, yields each non-empty page of drive records, and follows next-page tokens until Google has no more pages.

**Call relations**: paginate calls this when the stream is shared_drives. It uses list_or_empty to safely turn the response’s drives field into a list before yielding it to the sync engine.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches per-file collections: permissions, comments, or revisions. It works by visiting every file and then asking Google for that file’s related child records.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. First it gets all files using _paginate_files. For each file with a valid ID, it requests the chosen child collection page by page. It can filter child records by the stream’s cursor field, then adds the parent file’s ID and name to each child record before yielding it.

**Call relations**: paginate calls this for permissions, comments, and revisions. This function calls _paginate_files to get the parent files first, then uses list_or_empty to safely read each child collection from Google’s response. If Google refuses a particular child request because it is forbidden or missing, it skips that file’s child collection and continues with the rest.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into readable text for recall or display. For non-file streams, it falls back to the standard rendering behavior from the parent connector.

**Data flow**: It receives one synced record and the stream it came from. If the stream is not files, it returns the parent class’s rendering result. For a file record, it pulls out the name, MIME type, owners, and web link, then returns a title plus a short text body.

**Call relations**: The sync or recall layer calls this when it needs a human-readable version of a stored record. For file names it calls _str so that missing or non-text names become a safe empty string instead of causing formatting trouble.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into text only if it is already a string. It prevents unexpected data types from being treated as file names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this while building the display title for a file record. It keeps rendering simple and predictable when Google’s response is missing a name or contains an unexpected type.

*Call graph*: called by 1 (render).


### Meeting and spreadsheet content
Connectors that extract searchable content from Google Meet artifacts and Google Sheets tabs and rows.

### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `source sync`

Google Meet stores meeting history in several pieces: a conference record, transcript sessions, transcript lines, and sometimes smart notes created by Gemini. This connector pulls those pieces together so the rest of the system can treat one meeting as one recallable document. It starts by listing recent conference records from the Meet API. For each conference, it asks for transcript artifacts and smart-note artifacts. If a conference has neither, it is skipped as content-free, but its time can still move the cursor forward so future syncs do not keep rechecking the same empty window. The connector uses a one-day lookback when resuming, because Google may create transcripts or notes after the meeting ends. That is like checking yesterday’s mail again because a delayed package may have arrived late. For transcripts, it fetches the speaker-by-speaker entries and preserves the linked Google Docs destination. For smart notes, it also tries to read the Google Doc and inline its plain text; if the document is missing or access is denied, it keeps the link and continues. If Google Meet itself refuses access with an authentication or permission error, the stream is marked skipped rather than crashing the whole sync. Finally, it renders the gathered data into a readable Markdown-style page with meeting details, transcript dialogue, and AI summaries.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main fetch loop for the Google Meet stream. It asks Google for conference records page by page, enriches each meeting with transcripts and notes, and yields batches of finished records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor time. It builds a Google Meet request, optionally filtering from a slightly earlier time, reads conference pages, turns each conference into a full record, keeps only records with transcript or smart-note content, and outputs StreamPage objects with records plus the next cursor. If Google refuses access with a permission-style error, it changes that failure into a skipped stream message.

**Call relations**: The sync engine calls this when it wants Google Meet pages. During each page, it uses _lookback to widen the time filter, _max_start_time to choose the next saved position, and _conference_record to build each meeting-shaped record before handing the page back.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete in-memory record for one Google Meet conference. It gathers the conference’s transcripts and smart notes and packages them with meeting metadata such as start and end time.

**Data flow**: It receives the raw conference object from Google. It reads the conference resource name, fetches transcript artifacts and smart-note artifacts under that conference, converts each artifact into a simpler record, and returns one dictionary representing the meeting. The returned record includes an id, title, conference name, space, times, transcripts, and smart notes.

**Call relations**: paginate calls this for every conference Google returns. This function then fans out to _artifacts to find child items, _transcript to expand transcript details, and _smart_note to expand note details, before giving paginate a record it can include or skip.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind under a conference, such as all transcript sessions or all smart-note sessions. It hides Google’s page-by-page API shape from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference name, and the collection name to fetch. If the parent is missing, it returns an empty list. Otherwise it repeatedly asks Google for pages, collects the artifact lists from each response, follows page tokens, and returns one combined list.

**Call relations**: _conference_record calls this twice for each meeting: once for transcripts and once for smart notes. It supplies the raw artifact objects that _conference_record then passes onward to _transcript or _smart_note.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google into the connector’s simpler transcript shape. It includes basic metadata, the Google Docs destination, and the individual spoken entries.

**Data flow**: It receives a raw transcript artifact. It reads its name, state, time fields, and Docs destination, derives a short id from the resource name, fetches all transcript entries, and returns a dictionary ready to be stored inside the conference record.

**Call relations**: _conference_record calls this while building a meeting record. This function uses _docs_destination to preserve the linked document information and _transcript_entries to fill in the actual dialogue.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the lines of speech inside a transcript. Each entry becomes a small record with speaker, text, language, and timing information.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is blank, it returns no entries. Otherwise it reads entry pages from Google, converts each raw entry into a simpler dictionary, and returns the collected list. If Google says the entry resource is missing or forbidden, it returns whatever entries were collected instead of failing the whole meeting.

**Call relations**: _transcript calls this after it has identified a transcript session. The resulting entries later feed _dialogue during rendering, where they become human-readable speaker lines.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a simpler record. When possible, it also reads the linked Google Doc so the AI summary text is searchable directly.

**Data flow**: It receives a raw smart-note artifact. It extracts the id, resource name, state, timing, and Docs destination. If the artifact points to a Google Docs document, it asks _document_text for the plain text and adds that text as the note body when available. It returns the finished note dictionary.

**Call relations**: _conference_record calls this for each smart-note artifact found under a meeting. This function relies on _docs_destination for the link fields and hands the document id to _document_text when there is a readable Doc.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads the plain text from a Google Docs document referenced by a smart note. It lets the system store the summary text itself, not just a link.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely encodes the id for a URL, asks the Docs API for the document, and converts the structured document response into plain text. If the document is missing or access is denied, it returns an empty string rather than stopping the sync.

**Call relations**: _smart_note calls this only when a smart note has a Docs document id. After fetching the document, it passes the response to _plain_text, then gives the extracted text back to _smart_note to place in the note record.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored meeting record into readable text. The output is what a person or search system can later inspect as the meeting page.

**Data flow**: It receives one record and the stream description. For the Google Meet meeting-artifacts stream, it reads the title, conference details, transcripts, and smart notes, formats them into sections, and returns the page title plus the final text body. For any other stream, it falls back to the parent connector’s rendering behavior.

**Call relations**: The broader source framework calls this when it needs text for a fetched record. It uses _labeled for small metadata blocks, _transcripts_section for transcript content, and _smart_notes_section for Gemini note content.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript sessions for a meeting into one readable section. It combines transcript metadata with the spoken dialogue.

**Data flow**: It receives an unknown value that should contain transcript records. It safely treats non-lists as empty, then for each transcript formats state, times, and document link, builds dialogue from entries, and returns a Markdown-style transcript section. If there are no transcripts, it returns an empty string.

**Call relations**: render calls this while assembling the final meeting page. It delegates the metadata formatting to _labeled and the speaker-line formatting to _dialogue.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats Gemini smart notes into a readable AI summaries section. It includes both the note metadata and any fetched summary body text.

**Data flow**: It receives an unknown value that should contain smart-note records. It safely turns missing or invalid input into an empty list, then formats each note’s state, times, document link, and body text. It returns the completed section or an empty string if there are no notes.

**Call relations**: render calls this after adding the meeting header and transcript section. It uses _labeled for the small metadata block and _str to avoid printing non-text values as if they were valid text.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into a simple conversation format. It groups consecutive lines from the same speaker so the transcript reads less choppily.

**Data flow**: It receives an unknown value that should contain transcript entries. It walks through the entries, skips blank text, finds a readable speaker name, and builds lines like “Speaker: text.” If the same speaker continues, it appends the new text to the previous line. It returns the final dialogue as one string.

**Call relations**: _transcripts_section calls this when it needs the spoken part of a transcript. _dialogue uses _speaker to name each participant and _str to safely handle missing or non-text fields.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the structured JSON shape returned by the Google Docs API. It ignores document structure that is not paragraph text.

**Data flow**: It receives a Google Docs document record. It looks inside the document body, walks through paragraph elements, collects text-run content, joins the pieces, trims extra outer whitespace, and returns the plain text string.

**Call relations**: _document_text calls this after successfully fetching a Google Doc. It is the final step that turns Google’s nested document format into text that _smart_note can store as the note body.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls the Google Docs destination fields out of a transcript or smart-note artifact. It preserves both the document id and the export URL when Google provides them.

**Data flow**: It receives a raw artifact record. If the docsDestination field is not a dictionary, it returns an empty dictionary. Otherwise it extracts the document id and export link as strings and returns them under the connector’s simpler field names.

**Call relations**: _transcript and _smart_note both call this while normalizing Google artifacts. The returned fields let transcript records keep a durable document link and let smart notes try to fetch the linked Doc text.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest conference start time seen so far. That time becomes the cursor used to resume later syncs.

**Data flow**: It receives a list of conference records and the current cursor, which may be missing. It checks each conference start time and keeps the greatest timestamp string. It returns the updated cursor value.

**Call relations**: paginate calls this once per page of conference records. The result is placed on the StreamPage so the sync driver knows where to continue next time, even if none of the conferences had usable artifacts.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor one day earlier. It helps catch transcripts or notes that Google creates after the meeting has already ended.

**Data flow**: It receives an ISO timestamp string. It parses that time, subtracts the configured one-day lookback, formats it back into Google-friendly timestamp text, and returns that earlier time.

**Call relations**: paginate calls this before asking Google for incremental results. The earlier filter means the connector may recheck a small recent window rather than trusting that all artifacts existed at the first pass.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the last part of a Google resource name. It turns long path-like names into shorter ids that are easier to store and read.

**Data flow**: It receives a resource name string such as a slash-separated API path. If the name is present, it returns the text after the final slash; otherwise it returns an empty string.

**Call relations**: Several record-building functions use this when creating ids for conferences, transcripts, smart notes, and transcript entries. _speaker also uses it to turn a participant resource name into a readable fallback name.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses a readable speaker label for a transcript entry. If Google does not provide a usable participant id, it falls back to “Participant.”

**Data flow**: It receives the participant value from a transcript entry. It safely treats only strings as valid, extracts the last resource-name segment, and returns that as the speaker name, or returns “Participant” when nothing useful is available.

**Call relations**: _dialogue calls this for every transcript line it keeps. The speaker name it returns becomes the label at the start of each formatted dialogue line.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that accepts only real strings. It prevents missing fields, numbers, dictionaries, or other unexpected values from leaking into text output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Many functions use this while reading Google responses and rendering text. It keeps record building and page formatting predictable when optional API fields are absent or shaped differently than expected.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats label-and-value pairs into simple lines such as “start: ...”. It leaves out empty values so the rendered page is not cluttered with blank metadata.

**Data flow**: It receives a list of label and string-value pairs. It keeps only pairs with a non-empty value, joins them as one line per pair, and returns the resulting block of text.

**Call relations**: render uses this for the meeting header, while _transcripts_section and _smart_notes_section use it for artifact metadata. It provides one consistent style for small detail blocks across the final page.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Google Sheets. Without it, the system could not discover a user’s spreadsheets, notice which ones changed, or turn their tabs and cell rows into text that can be recalled later.

It works in three layers, like opening a filing cabinet. First, it asks Google Drive for spreadsheet files only, skipping trashed files and using a saved “last updated” time when possible so it does not reread everything every run. Second, for each spreadsheet file, it asks the Google Sheets API for spreadsheet details such as the title and list of tabs. If Drive can see a spreadsheet but Sheets refuses to open it, the connector still keeps the basic Drive information instead of losing the item entirely. Third, for the detailed content stream, it reads each tab’s grid of rows.

The connector exposes three streams: spreadsheets, sheets, and sheet_values. A “stream” is a category of records the sync system can pull. If the user’s permission grant is missing the needed Google scope, the connector marks that stream as skipped rather than treating the whole sync as broken. It also renders records into simple text: spreadsheet titles, tab names, and rows joined into readable lines.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 54–84)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the connector’s streams. It collects Google Sheets records into pages so the wider sync system can process them in batches instead of one at a time.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor time. It reads spreadsheet records, then either returns spreadsheets directly, expands them into tab records, or fetches tab cell values, depending on which stream was requested. It yields lists of records, and if Google refuses access because permission is missing, it turns that into a clean skipped-stream signal.

**Call relations**: The sync runner calls this when it wants records from one of the Google Sheets streams. It relies on _spreadsheet_records as the common starting point, hands spreadsheets to _sheet_records for tab summaries, and hands them to _sheet_value_records when full grid values are needed.

*Call graph*: calls 4 internal fn (__init__, _sheet_value_records, _spreadsheet_records, _sheet_records).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 86–111)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks Google Drive for spreadsheet files the user can access. It is careful to request only Google Sheets files and, when given a cursor, only files modified after the last sync point.

**Data flow**: It takes an HTTP client and an optional cursor timestamp. It builds a Drive search query, follows Google’s page tokens through all result pages, normalizes the returned file list into an ordinary list, and yields each non-empty page of file metadata. It does not return sheet contents, only Drive-level file information such as IDs, names, links, and timestamps.

**Call relations**: _spreadsheet_records calls this first because Drive is the place where spreadsheets are listed. This function supplies the raw file list that later steps enrich with Sheets-specific details.

*Call graph*: called by 1 (_spreadsheet_records); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_records`  (lines 113–143)

```
async def _spreadsheet_records(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function turns Drive file entries into full spreadsheet records. It adds spreadsheet titles, tab lists when available, links, and timestamps in the shape the sync system expects.

**Data flow**: It receives an HTTP client and optional cursor, then reads spreadsheet files from _iter_spreadsheet_files. For each valid file ID, it asks the Sheets API for spreadsheet metadata. If that metadata cannot be opened because of access or not-found errors, it falls back to the basic Drive file name and ID. It yields one completed spreadsheet record at a time.

**Call relations**: paginate calls this as the foundation for every stream. The spreadsheet stream uses its records directly, while the sheets and sheet_values streams use those same records as the parent information needed to produce tab-level or row-level records.

*Call graph*: calls 1 internal fn (_iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 145–167)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function reads the actual cell rows for each tab in a spreadsheet. It is what turns a spreadsheet from metadata into content the system can later recall.

**Data flow**: It receives an HTTP client and one spreadsheet record. It looks through the spreadsheet’s tab list, skips tabs without a usable title or ID, safely encodes the tab title for a web address, asks the Sheets API for row values, and yields one record per tab containing the grid plus spreadsheet and sheet identifiers.

**Call relations**: paginate calls this only for the sheet_values stream. It depends on _spreadsheet_records having already supplied a spreadsheet record with tab information, and it hands back value records that render can later turn into readable text.

*Call graph*: called by 1 (paginate); 1 external calls (quote).


##### `GoogleSheetsConnector.render`  (lines 169–188)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function converts a raw Google Sheets record into a plain text title and body. The output is meant for search, display, or recall, so it favors human-readable wording over raw API structure.

**Data flow**: It receives one record and the stream it came from. For spreadsheet records, it writes the spreadsheet title and its tab names; for sheet records, it writes the tab title and parent spreadsheet; for value records, it turns rows into lines of text. It returns a title plus a formatted text body.

**Call relations**: The broader source framework calls render after records have been fetched, when it needs a textual version of the data. This function uses _str to safely read optional text fields and _grid_text to turn cell grids into readable rows.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_sheet_records`  (lines 191–210)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper breaks one spreadsheet record into one record per sheet tab. It gives each tab its own stable ID so the sync system can treat tabs as separate items.

**Data flow**: It receives a spreadsheet record that may contain a list of sheets. It walks through that list, skips malformed entries, copies each tab’s original data, and adds the parent spreadsheet ID, parent title, tab title, and a combined tab ID. It returns a list of tab records.

**Call relations**: paginate uses this when the requested stream is sheets. It sits between spreadsheet discovery and tab-level syncing, turning one parent item into several child items.

*Call graph*: called by 1 (paginate).


##### `_grid_text`  (lines 213–218)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This helper turns a sheet’s cell grid into simple text. It makes rows readable by joining cells with vertical bars, like a lightweight table.

**Data flow**: It receives an unknown value that should be a list of rows. If the value is not a list, it returns an empty string. Otherwise, it keeps list-shaped rows, converts each cell to text, joins cells within a row, joins rows with newlines, and returns the resulting text block.

**Call relations**: render calls this for sheet_values records. It is the final small translation step that turns raw spreadsheet rows into recallable plain text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 221–222)

```
def _str(value: Any) -> str
```

**Purpose**: This helper safely extracts text only when a value is already a string. It prevents accidental values like numbers, objects, or missing fields from appearing in headings as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise, it returns an empty string. It does not change anything outside itself.

**Call relations**: render calls this while building titles and headings. It keeps the rendered output tidy when Google’s data is incomplete or not in the expected shape.

*Call graph*: called by 1 (render).

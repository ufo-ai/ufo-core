# Google and Microsoft productivity connectors  `stage-13.1.2`

This stage is a set of read-only “connectors,” or adapters, that let the system bring work data in from Google Workspace and Microsoft 365. It is behind-the-scenes support for the main sync loop: each connector talks to an outside service, notices what is new or changed, and turns that data into plain records the rest of the system can store, search, and recall.

The Google connectors cover the main Workspace tools. Gmail reads mailbox changes and formats emails. Google Calendar fetches events and attendees. Google Docs and Sheets turn documents and spreadsheets into readable text. Google Drive streams files, shared drives, permissions, comments, and revisions. Google Meet brings in transcripts and generated meeting notes.

The Microsoft connectors do the same job for Microsoft Graph, Microsoft’s API for work data. Teams reads teams, channels, chats, and messages. Outlook reads email, threads, contacts, calendar events, and folders. Together, these files act like translators between cloud productivity apps and the project’s common sync format.

## Files in this stage

### Google communications
Readers for Gmail and Google Calendar turn personal messages, events, attendees, and change feeds into searchable sync records.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync runs`

Gmail does not store an email as one simple text field. A message is a nested MIME tree, which means the readable parts may be buried inside separate plain-text or HTML sections, and the sender, recipients, and subject live in headers. This file is the adapter between that Gmail-shaped world and the system's simpler sync format.

The main class, GmailConnector, reads only one stream: messages. On a first run, it lists every Gmail message ID, then fetches each full message body. It also records Gmail's history ID, which works like a bookmark. On later runs, it asks Gmail what changed after that bookmark, fetches only newly added messages, and reports deleted messages as tombstones. If Gmail says the bookmark is too old, the connector raises a special “cursor expired” signal so the wider system knows to start over from a full sync. If the user's grant does not include Gmail read permission, it marks the stream as skipped instead of treating that as a system crash.

Once a raw Gmail message is fetched, helper functions flatten it into normal fields: subject, sender, recipients, labels, plain body, and HTML body. The render step then creates readable prose with From, To, Cc, Subject, and the best available message body. If only HTML exists, a small HTML parser strips tags and keeps readable text.

#### Function details

##### `GmailConnector.paginate`  (lines 75–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Gmail messages. It decides whether to do a full mailbox read or only read changes since the last saved Gmail history bookmark, then yields pages of records for the rest of the system to store.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If there is no cursor, it asks _backfill for all message IDs; if there is a cursor, it asks _history for added and deleted IDs. It fetches full message bodies in chunks, then outputs StreamPage objects containing new records, deleted IDs, and the next cursor. If Gmail refuses access because permission is missing, it turns that into a StreamSkipped signal.

**Call relations**: The sync framework calls this when it wants Gmail data. paginate delegates the “which IDs changed?” work to _backfill or _history, delegates body fetching to _fetch_bodies, and packages the result into StreamPage objects for the core sync engine.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 109–126)

```
async def _backfill(self, client: httpx.AsyncClient) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first full read of a mailbox. It lists every Gmail message ID so the connector can later fetch each message's full content.

**Data flow**: It starts with an empty list and repeatedly calls Gmail's message-list endpoint, following Gmail's next-page tokens until there are no more pages. It collects valid message IDs, then asks _seed_history_id for a starting history bookmark. It returns the full list of IDs and that bookmark.

**Call relations**: paginate calls this when there is no saved cursor. After _backfill returns IDs and a history marker, paginate fetches the bodies and emits them as synced records.

*Call graph*: calls 1 internal fn (_seed_history_id); called by 1 (paginate).


##### `GmailConnector._seed_history_id`  (lines 128–140)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str]) -> str | None
```

**Purpose**: This finds the Gmail history bookmark to save after a first full mailbox read. That bookmark lets the next run ask only for changes instead of reading the whole mailbox again.

**Data flow**: It receives the list of message IDs found during backfill. If the list is empty, it returns no bookmark. Otherwise it fetches the newest listed message in minimal form and reads its historyId field. If that message vanished before it could be fetched, it returns no bookmark; otherwise it returns the history ID when it is a string.

**Call relations**: _backfill calls this at the end of a full scan. The returned bookmark flows back through paginate as the next cursor for future incremental syncs.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 142–177)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail's change log after a saved history ID. It finds which messages were added and which were deleted since the last sync.

**Data flow**: It receives an HTTP client and a Gmail history ID. It walks Gmail's history pages, using _message_ids to pull message IDs out of added and deleted entries. It keeps the latest history ID Gmail returns, removes IDs that were both added and deleted from the added set, and returns added IDs, deleted IDs, and the new bookmark. If Gmail says the old bookmark expired, it raises CursorExpired so the system can run a full sync again.

**Call relations**: paginate calls this when a cursor exists. _history supplies the changed IDs, and paginate then fetches bodies for net-new messages and sends deletion tombstones on the final page.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 179–193)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns Gmail message IDs into full message records. It fetches each message from Gmail and reshapes the raw response into the connector's flat record format.

**Data flow**: It receives a list of message IDs. For each ID, it calls Gmail's message-get endpoint with full format. If a message disappeared and Gmail returns 404, it quietly skips that message. For every message it can fetch, it passes the raw Gmail response to _flatten_message and returns the resulting list of records.

**Call relations**: paginate calls this after _backfill or _history has identified which messages need full bodies. _fetch_bodies hands flattened records back to paginate, which places them in StreamPage results.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 195–214)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This creates the human-readable text version of a synced Gmail message. Without this, the system would mostly see Gmail's nested API structure instead of an email that reads like an email.

**Data flow**: It receives a flattened record and a stream description. For the messages stream, it extracts the subject, formats the sender and recipients, chooses the best body text, and returns a title plus a clean text document. For other streams, it falls back to the parent connector's rendering behavior.

**Call relations**: The broader source system calls render when it needs prose for recall or indexing. render relies on _str, _format_contact, _format_recipients, and _message_body to assemble the final readable message.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 217–226)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This pulls message IDs out of Gmail history entries. Gmail wraps each changed message inside a small nested object, so this helper extracts just the useful ID strings.

**Data flow**: It receives any value, usually a list from Gmail such as messagesAdded or messagesDeleted. It ignores entries that are not dictionaries or do not contain a usable message ID. It returns a list of valid non-empty ID strings.

**Call relations**: _history calls this while reading Gmail's change log. The IDs it returns are added to the sets that decide which messages paginate will fetch or delete.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 229–256)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Gmail API message into a simple record the sync system can store. It lifts out the important headers, body text, labels, and sender/recipient details.

**Data flow**: It receives Gmail's full message JSON. It reads selected headers such as From, To, Cc, and Subject, extracts plain and HTML bodies, parses addresses into structured fields, copies labels, and marks the message as outbound if it has Gmail's SENT label. It returns one flat dictionary with predictable keys.

**Call relations**: _fetch_bodies calls this after downloading each full message. _flatten_message uses _extract_bodies, _parse_first_address, and _addresses to turn Gmail's nested response into the record later used by render.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 259–273)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail message's MIME tree for readable body content. It looks for the first plain-text part and the first HTML part.

**Data flow**: It receives the message payload dictionary. Its inner walk function visits the payload and any child parts, decodes body data for text/plain and text/html parts, and remembers the first one of each kind. It returns a pair: plain text if found, and HTML text if found.

**Call relations**: _flatten_message calls this while building the flat record. The extracted bodies later feed _message_body, which chooses what text should appear in the rendered email.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 263–270)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive searcher inside _extract_bodies. It walks through the nested email parts like opening folders inside folders until it finds readable text pieces.

**Data flow**: It receives one MIME part. If that part is a plain-text or HTML part with encoded data, it decodes the data with _b64url_decode and stores it if that type has not already been found. Then it repeats the same process for each child part.

**Call relations**: _extract_bodies starts this helper on the top-level payload. walk calls _b64url_decode whenever it finds a body part that Gmail encoded as URL-safe base64.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 276–282)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail's body text encoding into normal text. Gmail stores message part bodies as URL-safe base64, which is a way of representing bytes using safe text characters.

**Data flow**: It receives an encoded string. It adds any missing padding Gmail left off, decodes the URL-safe base64 bytes, and turns them into UTF-8 text, replacing invalid characters if needed. If decoding fails, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this when it finds an encoded plain-text or HTML message part. The decoded text becomes the body content stored in the flattened record.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 285–292)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This reads the first email address from a header such as From. It separates the mailbox address from the display name.

**Data flow**: It receives a header string or nothing. If there is no header or no parsed address, it returns two empty values. Otherwise it uses the standard email parser, lowercases the email address, and returns the address plus the display name if one exists.

**Call relations**: _flatten_message calls this for the From header. The result becomes from_handle and from_display_name, which render later turns into the visible From line.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 295–302)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This reads all usable email addresses from a recipient header such as To or Cc. It turns one header string into a list of small contact records.

**Data flow**: It receives a header string or nothing. If there is no header, it returns an empty list. Otherwise it parses names and addresses, ignores entries without an address, lowercases each address, and returns dictionaries with handle and display_name fields.

**Call relations**: _flatten_message calls this for To and Cc headers. The resulting contact lists are later passed to _format_recipients when render builds readable recipient lines.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 305–310)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This formats one contact for display in an email header line. It uses the familiar form “Name <address>” when a name is available, or just the address otherwise.

**Data flow**: It receives a possible email handle and display name. If the handle is not a real non-empty string, it returns an empty string. If there is a display name, it combines name and address; otherwise it returns the address alone.

**Call relations**: render calls this for the sender, and _format_recipients calls it for each recipient. It provides the small formatting building block used in From, To, and Cc lines.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 313–320)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient contact records into one readable line. It creates the comma-separated text used after To or Cc.

**Data flow**: It receives any value, normally a list of contact dictionaries. If the value is not a list, it returns an empty string. For each dictionary item, it formats the contact with _format_contact and joins the results with commas.

**Call relations**: render calls this while building the To and Cc header lines. It delegates individual contact formatting to _format_contact.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 323–331)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for a message. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail's short snippet if no full body is available.

**Data flow**: It receives a flattened message record. If body_text is a non-empty string, it returns the trimmed plain text. If not, it checks body_html and uses _HtmlText.extract to strip tags and keep readable words. If neither body exists, it returns the snippet when present, or an empty string.

**Call relations**: render calls this to fill in the main content below the email headers. It is the bridge between stored raw body fields and the prose that the recall system indexes.

*Call graph*: called by 1 (render).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns an optional value into a string only when it already is one. It avoids accidentally rendering non-text values as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: render calls this for the subject before building the title and header block. It keeps the render path simple and safe when Gmail data is missing or oddly shaped.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 342–344)

```
def __init__(self) -> None
```

**Purpose**: This prepares the small HTML-to-text parser used for email bodies. It creates the storage where readable text pieces will be collected.

**Data flow**: It receives no outside data beyond the new parser instance. It initializes the standard HTML parser with automatic character-reference conversion, then creates an empty list for text fragments. The result is a parser ready to be fed HTML.

**Call relations**: _HtmlText.extract creates a parser instance, which runs this initializer before feeding it raw HTML. Later parser callbacks add text and line breaks to the list created here.


##### `_HtmlText.extract`  (lines 347–352)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This turns an HTML email body into plain readable text. It removes tags and attributes while keeping words and sensible line breaks.

**Data flow**: It receives a raw HTML string. It creates an _HtmlText parser, feeds the HTML through it, joins the collected text pieces, normalizes extra spaces on each line, removes empty lines, and returns the cleaned text.

**Call relations**: _message_body calls this when a message has HTML but no plain-text body. During parsing, the parser's handle_data, handle_starttag, and handle_endtag methods collect content and line breaks.


##### `_HtmlText.handle_data`  (lines 354–355)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records actual text found inside HTML. For example, it keeps the words inside a paragraph while ignoring the paragraph tag itself.

**Data flow**: It receives a text chunk from the HTML parser. It appends that chunk to the parser's internal list. It does not return anything; it changes the parser's collected output.

**Call relations**: The standard HTML parser calls this automatically while _HtmlText.extract feeds it HTML. The collected chunks are later joined and cleaned by extract.


##### `_HtmlText.handle_starttag`  (lines 357–359)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an HTML block-level tag starts. Block-level tags are tags like paragraphs or list items that usually create visual separation on the page.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the collected text pieces. It ignores all attributes and returns nothing.

**Call relations**: The standard HTML parser calls this during _HtmlText.extract. Its newline markers help extract preserve readable paragraph-like spacing after tags are removed.


##### `_HtmlText.handle_endtag`  (lines 361–363)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when an HTML block-level tag ends. It helps stop separate paragraphs, table cells, or list items from running together.

**Data flow**: It receives the closing tag name. If the tag is one of the known block tags, it appends a newline marker to the collected text pieces. It returns nothing and only changes the parser's internal text list.

**Call relations**: The standard HTML parser calls this while _HtmlText.extract processes HTML. Together with handle_starttag and handle_data, it produces the text that extract later cleans and returns.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the rest of the system. Without it, calendar meetings could not be pulled in as recallable content, and the system would not know which invitees belong to which event.

It defines two streams of data. The first, `calendar_events`, stores one record per calendar event. The second, `event_attendees`, stores one record per invited person, like making a separate guest list row for every meeting attendee. Both streams use Google Calendar's change-tracking token, called a `syncToken`. On the first run there is no token, so the connector looks back 90 days and asks Google for events, including deleted ones. At the end, Google returns a new token. Later runs send that token so only changes since the last sync are fetched.

The connector carefully treats deleted events as tombstones, meaning it reports their IDs so the local copy can remove them. If Google says the saved token is too old, it raises `CursorExpired` so the wider sync system can start fresh. If the user's permission grant does not include calendar access, it raises `StreamSkipped` instead of failing the whole run.

The helper functions reshape Google's nested event data into simpler records, normalize attendee emails, classify attendee roles, and turn event times into consistent text. The `render` method then creates a readable note-like body for calendar events.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches Google Calendar events page by page and turns them into sync pages for either events or attendees. It uses a saved cursor when available so later runs only read changes instead of re-reading the whole calendar.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If there is a cursor, it sends it to Google as a change-tracking token; if not, it asks for events from the last 90 days. For each page from Google, it separates normal records from cancelled events, reshapes records into the stream's expected form, and yields `StreamPage` objects containing new or updated records, deleted IDs, and finally the next cursor. If Google rejects an expired token or missing permission, it turns those HTTP errors into clear sync-level signals.

**Call relations**: The sync engine calls this when it needs data from the Google Calendar connector. During the walk, it hands each raw event to `_flatten_event` for the main event stream or `_flatten_attendees` for the attendee stream. It packages the results into `StreamPage` objects so the core sync code can write updates, delete cancelled events, and remember the next cursor.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Builds a human-readable title and body for a synced calendar event. This is what makes an event useful as recallable text instead of just raw fields.

**Data flow**: It receives a flattened record and the stream it came from. For `calendar_events`, it reads fields such as title, start and end time, location, attendees, and description, then combines them into a simple text block. For other streams, it falls back to the base connector's default rendering. It returns a pair: the display title and the rendered body text.

**Call relations**: The larger source framework calls this when it needs text to index or display for a synced record. It uses `_str` to safely treat missing or non-text titles as an empty string, then assembles the calendar event into a readable form.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns one raw Google Calendar event into the simpler event record used by this system. It keeps the meeting's main facts and folds in a compact list of attendees.

**Data flow**: It receives one event dictionary from Google's API. It reads fields like ID, creation time, update time, summary, description, location, start and end, organizer, recurrence link, and attendees. It normalizes organizer and attendee email addresses to lowercase, converts Google time objects into text timestamps, marks all-day events, and returns one flat dictionary ready to sync.

**Call relations**: It is used by `GoogleCalendarConnector.paginate` when syncing the `calendar_events` stream. It calls `_attendee` to simplify each attendee and `_parse_when` to make Google's two different time formats look consistent.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Creates a small, clean attendee summary for embedding inside an event record. It preserves who the attendee is and how they responded to the invitation.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee's email address, copies the display name when present, translates Google's response value into this system's preferred wording, and returns a small dictionary with those values.

**Call relations**: It is called by `_flatten_event` while building the attendee list inside a calendar event record. It does not fetch or store anything itself; it simply converts one nested attendee object into a cleaner shape.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one calendar event into separate attendee records, one per invited person. This lets the system treat attendee information as its own stream rather than only as text inside an event.

**Data flow**: It receives one raw Google event. It reads the event ID, timestamps, organizer email, and attendee list. For each attendee with a valid email address, it creates a unique ID made from the event ID and attendee email, records their role, response, display name, and whether the attendee represents the calendar owner, then returns the list of attendee rows.

**Call relations**: It is used by `GoogleCalendarConnector.paginate` when syncing the `event_attendees` stream. For each attendee row, it asks `_attendee_role` to decide whether the person is the organizer, a room or resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what kind of participant an attendee is in a meeting. This turns Google's attendee flags into a simple role label.

**Data flow**: It receives one attendee dictionary and a separate yes-or-no answer for whether that attendee is the organizer. It checks organizer, resource, and optional flags in priority order. It returns one role string: `organizer`, `resource`, `optional`, or `required`.

**Call relations**: It is called by `_flatten_attendees` while creating one row per invitee. Its result becomes the attendee record's role, which makes later searching or filtering easier.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar's event time format into one text timestamp. It supports both timed events and all-day events.

**Data flow**: It receives a value that should be Google's start or end object. If it contains `dateTime`, it returns that value as text. If it contains an all-day `date`, it turns the date into a midnight UTC-style timestamp. If the input is missing or not shaped as expected, it returns nothing.

**Call relations**: It is called by `_flatten_event` for event start and end times. This keeps the rest of the connector from needing to care whether Google represented the event as timed or all-day.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents non-text or missing titles from leaking into rendered output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: It is called by `GoogleCalendarConnector.render` when preparing the event title. This small guard keeps rendering simple and avoids surprising output when a record lacks a normal title.

*Call graph*: called by 1 (render).


### Google workspace content
Readers for Google Docs, Drive, Meet, and Sheets extract documents, files, meeting artifacts, and spreadsheets into recallable text records.

### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Google Docs. Its job is to find Google Docs through Google Drive, fetch each document from the Google Docs API, and reshape the result into records the rest of the system can store and recall later. Without this file, Google Docs would not become searchable source material inside the system.

The sync starts with Drive, because Drive is where Google lists files. The connector asks Drive for files whose type is “Google document,” skips trashed files, and orders them by modification time. If the system already has a saved cursor, it asks Google only for files changed after that time. This is like checking only the mail that arrived after your last pickup instead of rereading the whole mailbox.

For each Drive file, the connector fetches the full document from the Docs API. If one listed document cannot be opened or has disappeared, it creates a small stub record instead of failing the whole sync. But if Drive itself refuses access, the stream is skipped because the connector cannot discover documents at all.

Finally, `render` turns Google’s nested document structure into ordinary prose. Google stores text inside paragraph elements and text runs; this file walks that tree and joins the visible text into one readable body.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main sync loop for Google Docs records. It lists changed Google Docs, fetches each document, combines Drive metadata with document content, and yields records in batches so the rest of the system can process them.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced modification time. It asks `_iter_doc_files` for Drive file pages, fetches each document with `_document`, builds a normalized record with IDs, title, URL, timestamps, file metadata, and document content, then outputs lists of up to 100 records. If Google refuses access to the Drive or Docs listing, it turns that refusal into a `StreamSkipped` signal instead of crashing the whole run.

**Call relations**: During a source sync, the broader connector framework calls this method to get pages of Google Docs data. It relies on `_iter_doc_files` to discover candidate files and `_document` to fetch each file’s full Docs content. If the account lacks permission, it raises `StreamSkipped` so the sync system can record that this stream could not be read.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This helper fetches one Google Doc in full from the Google Docs API. It also protects the sync from failing just because a single document is no longer readable.

**Data flow**: It receives an HTTP client and a Google Drive file ID. It requests the matching document from Google Docs and returns the document data. If Google says the document is forbidden or missing, it returns a minimal record containing only the document ID; for other errors, it lets the error continue upward.

**Call relations**: `paginate` calls this once for each file discovered through Drive. Its result is folded into the final record that `paginate` yields, so the rest of the sync can continue even when one document cannot be opened.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper walks through Google Drive’s file listing and finds Google Docs the account can see. It supports incremental sync by asking Google only for documents modified after the saved cursor.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive query for untrashed Google Docs, adds a “modified after this time” filter when a cursor exists, then repeatedly requests Drive pages using Google’s next-page token. Each response’s `files` value is made safely into a list with `list_or_empty`, and non-empty file pages are yielded. When there is no next-page token, it stops.

**Call relations**: `paginate` calls this at the start of the sync to discover which documents need fetching. It talks to Drive rather than Docs because Drive is the service that can list files, while `_document` later fetches the full content for each listed file.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a title and a plain-text document body for recall or indexing. It adds a simple heading so the rendered text keeps useful context about where it came from.

**Data flow**: It receives one synced record and the stream description. It reads the title if present, asks `_plain_text` to extract the document’s visible paragraph text, creates a heading that includes the connector and stream name, and returns the title plus the final rendered text.

**Call relations**: The connector framework calls this when it needs a human-readable version of a synced record. It delegates the detailed Google Docs text extraction to `_plain_text`, then wraps that text in a consistent source heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This helper extracts readable text from Google’s nested document format. Google Docs stores text inside paragraphs and text runs, so this function walks that structure and joins the pieces in order.

**Data flow**: It receives a document record. It looks for `body.content`, then scans each content item for a paragraph, each paragraph for elements, and each element for a text run. Any string content it finds is appended to a list, then all pieces are joined and trimmed. If the body is missing or shaped differently, it simply returns an empty string or whatever valid text it can find.

**Call relations**: `render` calls this whenever a synced Google Docs record needs to become plain prose. It does not fetch data or change the record; it only translates Google’s structured document data into text the rest of the system can index or display.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `source sync`

This connector is the bridge between the project and Google Drive. Without it, the system would not know which Google Drive web addresses to call, how to page through long result lists, how to notice deleted files, or how to recover when Google says an old change marker is no longer valid.

The main stream is `files`. On the first run, it asks Google Drive for all non-trashed files, sorted by modification time. At the end of that first scan, it asks Google for a change token, which is like a bookmark saying, “next time, start from here.” On later runs, it uses that bookmark to ask only for changes. If a file was removed or moved to trash, it returns a delete marker instead of a normal record.

Other streams work differently. Shared drives are reread fully each time. Permissions, comments, and revisions are fetched by first listing every file, then asking Google for that file’s smaller child lists. If the user’s permission grant does not include Drive access, the connector reports the stream as skipped rather than treating the whole run as broken.

The file also defines a simple `render` view for Drive files, turning raw fields like name, MIME type, owners, and link into readable text.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading Google Drive streams. Given a stream name, it chooses the right way to fetch that kind of data and yields pages of results for the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the saved bookmark from a previous run. For the `files` stream, it either reads all current files or reads changes since the cursor. For `shared_drives`, it reads drives directly. For permissions, comments, and revisions, it walks through files and fetches each file’s child records. It yields lists of records or `StreamPage` objects, which can include records, deletes, and the next cursor. If Google refuses access with an authorization-style error, it turns that into a clean “stream skipped” result.

**Call relations**: The sync runner calls this when it wants data from a Google Drive stream. This function then hands work to `_paginate_files`, `_paginate_file_changes`, `_paginate_shared_drives`, `_paginate_file_children`, or `_start_page_token` depending on what is being synced. It also creates `StreamPage` results when it needs to pass back a new cursor.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Google Drive file records in batches. It is used for the first full file scan and also as the file list that child streams rely on.

**Data flow**: It starts with an HTTP client and an optional modification-time cursor. It builds a Google Drive file search that ignores trashed files, optionally limits results to files modified after the cursor, and asks for a fixed set of useful fields. For each Google page, it safely turns the `files` value into a list and yields it if there are records. It follows Google’s `nextPageToken` until no more pages remain.

**Call relations**: `paginate` calls this during a full `files` sync. `_paginate_file_children` also calls it first so it knows which files to inspect for permissions, comments, or revisions. It relies on `list_or_empty` to avoid breaking if Google returns a missing or non-list value where a list is expected.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current change bookmark. The bookmark lets the next run fetch only changes instead of scanning every file again.

**Data flow**: It receives an HTTP client, calls Google’s start-page-token endpoint, reads the `startPageToken` field, and returns it only if it is a non-empty string. If the response does not contain a usable token, it returns nothing.

**Call relations**: `paginate` calls this after the first full file listing finishes. The returned token is wrapped in a `StreamPage` so the core sync system can save it as the cursor for the next run.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads what changed in Google Drive since the last saved change token. It is what makes later syncs efficient and lets the system notice deleted or trashed files.

**Data flow**: It receives an HTTP client and a cursor token from an earlier run. It calls Google’s changes endpoint page by page. For each change, it checks the file id, separates deleted or trashed files into a delete list, and keeps normal changed files as records. It yields `StreamPage` objects containing changed records, deleted file ids, and the next cursor. If Google says the token is too old, it raises `CursorExpired` so the larger system can do a fresh full sync.

**Call relations**: `paginate` calls this for the `files` stream when a cursor already exists. This function creates `StreamPage` objects for each batch and uses `list_or_empty` to safely read the changes list. It signals `CursorExpired` when Google returns the specific “old token” condition, which tells the core sync flow that the saved bookmark can no longer be trusted.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the shared drives visible to the connected Google account. Shared drives are not treated as a change stream here; they are simply reread in full each run.

**Data flow**: It receives an HTTP client, requests shared drives from Google in pages, extracts the `drives` list from each response, and yields non-empty batches. It follows `nextPageToken` until Google reports there are no more pages.

**Call relations**: `paginate` calls this when the requested stream is `shared_drives`. Like the other paging helpers, it uses `list_or_empty` so an unexpected empty response shape becomes an empty batch rather than a crash.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches per-file sub-records such as permissions, comments, and revisions. It works by visiting every file first, then asking Google for the requested child collection for each file.

**Data flow**: It receives an HTTP client, a child stream description, and an optional cursor. First it reads all current files using `_paginate_files`. For each file with a valid id, it calls the matching child endpoint, such as that file’s permissions or comments endpoint. It pages through the child results, optionally filters them by the stream’s cursor field, and yields records enriched with the parent file id and file name. If Google says a child collection is forbidden or missing for one file, it skips that file’s child list and keeps going.

**Call relations**: `paginate` calls this for the `permissions`, `comments`, and `revisions` streams. This function depends on `_paginate_files` to provide the parent files, then uses `list_or_empty` to normalize each child response before yielding records back to the sync runner.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a raw Google Drive file record into a small readable text summary. That text can be used by the rest of the system when it needs a human-friendly representation of a synced file.

**Data flow**: It receives one record and the stream it came from. If the stream is not `files`, it lets the base connector render it normally. For file records, it reads the name, MIME type, owners, and web link, builds a title, and returns both the title and a short text body. It uses `_str` to avoid treating non-text names as real titles.

**Call relations**: The broader source framework calls this when it wants to turn synced records into recallable text. For Google Drive file records, this function performs the custom formatting itself. For all other Google Drive streams, it hands rendering back to the parent `RestConnector` behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper returns a value only if it is actually text. It prevents the renderer from accidentally using numbers, objects, or missing values as a file title.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: `GoogleDriveConnector.render` calls this when reading a file name. It keeps the rendering code simple and safe by centralizing this small type check.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `source sync`

This connector is like a careful librarian for Google Meet. It visits the Google Meet REST API, asks for recent conference records, then looks inside each meeting for generated artifacts: transcripts and Gemini smart notes. A conference only becomes an output page if it has at least one of those artifacts.

The file also deals with the timing problem of meeting artifacts. Transcripts and notes can appear after the meeting ends, so when syncing from a saved cursor it looks back one day. That means it may re-check some meetings, but it avoids missing late-generated notes. At the same time, it still advances the cursor even through meetings with no artifacts, so the sync does not keep rereading the same empty window forever.

For each meeting, it fetches transcript sessions, transcript entries, and smart-note records. If a smart note points to a Google Doc and the same account can read it, the connector pulls the plain text out of the document. If Google says the document is missing or forbidden, the connector keeps the link and continues instead of failing the whole run. If the Meet API itself refuses access, the stream is marked skipped rather than broken. Finally, the connector renders all this structured data as a clean Markdown-like page.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches pages of Google Meet conference records and turns each useful meeting into a stream page for the sync engine. It is the main doorway from the generic source framework into this Google Meet-specific logic.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It builds a Meet API request, optionally adds a one-day lookback filter, downloads conference pages, enriches each conference with transcript and smart-note data, and yields batches of records plus the newest start time to save as the next cursor. If Google refuses access with an authorization-style error, it turns that into a skipped stream instead of a hard failure.

**Call relations**: The sync framework calls this when it needs Google Meet data. It uses _lookback to widen the cursor window, _max_start_time to choose the next cursor, and _conference_record to turn each raw Meet conference into a richer record before wrapping the result in StreamPage.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Builds one complete meeting record from a raw Google Meet conference object. It gathers the meeting’s transcripts and AI notes so the rest of the system can treat the meeting as one recallable item.

**Data flow**: It receives a conference dictionary from the Meet API. It reads the conference resource name, asks for transcript and smart-note artifacts under that conference, converts each artifact into a simpler local shape, and returns one dictionary containing meeting identity, timing, space information, transcripts, and smart notes.

**Call relations**: paginate calls this for every conference it sees. This function then delegates the collection work to _artifacts and delegates artifact-specific shaping to _transcript and _smart_note.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: Downloads all artifacts of one kind, such as transcripts or smart notes, for a conference. It hides the repeated page-by-page API fetching needed by Google’s endpoints.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the artifact collection name to read. If there is no parent name it returns an empty list. Otherwise it requests artifact pages until Google stops returning a next-page token, collects the artifact objects, and returns them as a list.

**Call relations**: _conference_record calls this twice: once for transcripts and once for smart notes. It uses the shared list_or_empty helper so missing or non-list API fields become a safe empty list.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one transcript session from Google’s format into the connector’s simpler record format. It also attaches the individual spoken entries for that transcript.

**Data flow**: It receives a transcript dictionary. It extracts a short id, basic state and timing fields, any Google Docs destination link, then calls _transcript_entries to fetch the speaker-by-speaker text. It returns a transcript record containing both metadata and entries.

**Call relations**: _conference_record calls this for every transcript artifact found under a meeting. It relies on _docs_destination for linked document information and _transcript_entries for the actual spoken text.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: Downloads the line-by-line transcript text for one transcript session. This is what turns a transcript from a mere artifact record into readable dialogue.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty it returns no entries. Otherwise it fetches transcript-entry pages, extracts each entry’s id, participant, text, language, and timing, and returns the collected list. If Google reports a missing or forbidden backing document during this step, it returns whatever entries it already has instead of failing.

**Call relations**: _transcript calls this after shaping the transcript metadata. The rendered transcript later flows through _dialogue, which turns these entries into speaker-labeled lines.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one Google Meet smart-note artifact into a simpler record and, when possible, fills it with the note’s Google Docs text. This lets AI summaries appear directly in the saved page instead of only as links.

**Data flow**: It receives a smart-note dictionary. It extracts id, name, state, timing, and any Docs destination fields. If a document id is present, it asks _document_text for the document’s plain text and stores that text as the note body when available. It returns the completed smart-note record.

**Call relations**: _conference_record calls this for every smart-note artifact under a meeting. It uses _docs_destination for the linked document fields and hands off to _document_text when there is a document worth reading.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: Reads the plain text from a Google Docs document referenced by a smart note. It makes smart-note content searchable even when the Meet artifact only points to a document.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely quotes the id for use in a URL, asks the Docs API for the document, and passes the returned document structure to _plain_text. If the document is forbidden or missing, it returns an empty string so the sync can continue.

**Call relations**: _smart_note calls this only when a smart note includes a document id. This function performs the Docs API fetch, then hands the nested document data to _plain_text to extract readable content.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one structured Google Meet record into the readable page text stored by the system. It gives people a clean meeting page instead of raw API JSON.

**Data flow**: It receives a record and a stream description. For the meeting-artifacts stream, it reads the meeting title and metadata, formats the conference details, transcript sections, and AI-summary sections, and returns a title plus the finished text body. For any other stream, it falls back to the parent connector’s rendering behavior.

**Call relations**: After records have been fetched by paginate and its helper methods, the source framework uses render to produce the final human-readable representation. It calls _labeled, _transcripts_section, and _smart_notes_section to assemble the page from smaller parts.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: Formats all transcript records for a meeting into a readable text section. It gives each transcript its basic details and its spoken dialogue.

**Data flow**: It receives any value that should contain transcript records. It treats missing or invalid values as an empty list, then for each transcript formats metadata such as state, times, and document link, combines that with dialogue text, and returns one transcript section string. If there are no transcripts, it returns an empty string.

**Call relations**: GoogleMeetConnector.render calls this while building the final meeting page. It uses _dialogue to turn transcript entries into speaker-labeled conversation and _labeled to format small metadata blocks.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: Formats all AI-generated meeting notes into a readable section. It includes both the note’s metadata and the note body when the connector was able to read it from Google Docs.

**Data flow**: It receives any value that should contain smart-note records. It turns missing or invalid input into an empty list, then for each note formats state, timing, document link, and body text. It returns the complete AI summaries section, or an empty string when there are no notes.

**Call relations**: GoogleMeetConnector.render calls this while assembling the final page. It depends on _labeled for the metadata block and on _str to safely treat non-text note bodies as empty text.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: Turns raw transcript entries into a clean conversation transcript. It also joins back-to-back entries from the same speaker so the output reads more naturally.

**Data flow**: It receives any value that should contain transcript entries. It skips empty text, derives a readable speaker name from each participant, and builds lines like “Speaker: text.” If the same speaker continues speaking in the next entry, it appends the new text to the previous line. It returns the final dialogue as plain text.

**Call relations**: _transcripts_section calls this when it needs the spoken part of a transcript. It uses _speaker to choose a display name and _str to avoid crashes when fields are missing or not text.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: Extracts readable text from the nested structure returned by the Google Docs API. It strips away document layout structure and keeps the actual words.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the document body, walks through paragraph elements, collects text-run contents, joins them together, trims surrounding whitespace, and returns the resulting string.

**Call relations**: _document_text calls this after downloading a Google Docs document. This keeps the API-reading step separate from the document-shape parsing step.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: Pulls the Google Docs destination fields out of a Meet transcript or smart-note artifact. These fields are important because they give a durable document id and link even when inline text is unavailable.

**Data flow**: It receives an artifact dictionary. If the artifact has a valid docsDestination object, it extracts the document id and export URL as safe strings and returns them in a small dictionary. If not, it returns an empty dictionary.

**Call relations**: _transcript and _smart_note call this while building their simpler local records. The returned fields later appear in rendered transcript and AI-summary sections.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: Chooses the newest conference start time seen in a batch, preserving the existing cursor when nothing newer appears. This is how the connector knows where to resume next time.

**Data flow**: It receives a list of conference dictionaries and the current cursor. It checks each conference startTime string and keeps the largest one, then returns that value. It does not change the conferences themselves.

**Call relations**: paginate calls this after downloading each page of conferences. The returned value becomes the next_cursor placed on the StreamPage for the sync driver.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: Moves a saved cursor one day into the past so late-created transcripts or notes can still be found. This is a safety buffer for Google Meet artifacts that appear after the meeting ends.

**Data flow**: It receives an ISO-format timestamp string. It parses it as a date and time, subtracts the configured one-day lookback, formats it back into Google’s timestamp style, and returns that string for use in the API filter.

**Call relations**: paginate calls this when it already has a cursor. The returned timestamp is inserted into the Meet API filter before fetching conference records.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: Gets the short final id from a longer Google resource name. For example, it turns a path-like name into just the last piece after the final slash.

**Data flow**: It receives a resource name string. If the string is present, it splits from the right on “/” and returns the last part; otherwise it returns an empty string.

**Call relations**: Several record-building helpers use this to create compact ids for conferences, transcripts, smart notes, transcript entries, and speakers. _speaker also uses it as part of making participant names readable.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: Creates a readable speaker label for a transcript participant. It falls back to “Participant” when Google does not provide a usable name.

**Data flow**: It receives a participant value of any kind. It first keeps it only if it is text, then takes the final resource-id part, and returns that as the speaker label. If no label can be found, it returns “Participant.”

**Call relations**: _dialogue calls this for each transcript entry while building speaker-labeled conversation lines. It relies on _str and _resource_id for safe cleanup.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: Safely treats a value as text only when it really is a string. This prevents missing or unexpected API values from leaking into rendered text or causing type problems.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: This small helper is used throughout the connector whenever API fields are copied into ids, titles, labels, links, or text output. It keeps the rest of the code simpler by centralizing this safety check.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Formats a small set of label-and-value pairs as readable lines, skipping empty values. It is used for metadata blocks such as meeting time, state, and document link.

**Data flow**: It receives a list of label and text-value pairs. It keeps only pairs with a non-empty value, formats each as “label: value,” joins them with newlines, and returns the resulting block.

**Call relations**: GoogleMeetConnector.render, _transcripts_section, and _smart_notes_section call this while building the final page text. It gives those larger renderers a consistent way to show metadata without blank lines for missing fields.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `source sync run`

This connector is the bridge between a user’s Google account and the project’s source-sync system. Without it, the system could not discover Google Sheets, split them into useful pieces, or turn their contents into readable text for later recall.

The file works in three layers. First, it asks Google Drive for spreadsheet files, because Drive is the service that lists files. It filters for real Google Sheets, ignores trashed files, and can ask only for files changed after the last saved time marker. That makes the main spreadsheet stream incremental, meaning later syncs can skip old unchanged files.

Second, for each spreadsheet file, it asks the Google Sheets API for spreadsheet details, especially the tab list. If Drive can list a file but Sheets refuses to open it, the connector keeps a smaller record from Drive metadata instead of failing the whole sync.

Third, it can expand each spreadsheet into tab records, and then read each tab’s cell values row by row. The `render` method turns these raw API records into plain text: spreadsheet tab names, a tab’s parent spreadsheet, or a grid of cells separated by vertical bars. If Google refuses access because the account lacks permission or API scope, the stream is marked as skipped rather than treated as a broken run.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 54–84)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main paging loop for the connector. It decides which Google Sheets stream is being requested, gathers the right kind of records, and returns them in batches so the sync system can process them steadily instead of all at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor time. It reads spreadsheet records, then either keeps them as spreadsheet records, expands them into sheet-tab records, or fetches each tab’s cell values. It yields lists of records, and if Google says the account is not allowed to read the data, it turns that into a clean stream skip.

**Call relations**: The sync framework calls this when it needs records from the Google Sheets source. It asks `_spreadsheet_records` for the base spreadsheet list, hands spreadsheets to `_sheet_records` when the requested stream is tabs, and hands them to `_sheet_value_records` when the requested stream is cell grids. If access is refused, it raises `StreamSkipped` so the wider sync can record a skipped stream instead of crashing.

*Call graph*: calls 4 internal fn (__init__, _sheet_value_records, _spreadsheet_records, _sheet_records).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 86–111)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists spreadsheet files from Google Drive. It exists because Drive, not Sheets, is the place where Google file discovery happens.

**Data flow**: It receives an HTTP client and an optional cursor time. It builds a Drive search for non-trashed Google Sheets, optionally only those modified after the cursor, follows Drive page tokens, cleans the returned file list with `list_or_empty`, and yields each page of file metadata.

**Call relations**: `_spreadsheet_records` calls this first, before it can ask the Sheets API for richer spreadsheet details. This function only discovers candidate files; the next step decides what each spreadsheet record should look like.

*Call graph*: called by 1 (_spreadsheet_records); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_records`  (lines 113–143)

```
async def _spreadsheet_records(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function turns Drive file entries into full spreadsheet records. It combines Drive metadata, like modified time and web link, with Sheets metadata, like spreadsheet title and tab list.

**Data flow**: It reads pages of Drive files from `_iter_spreadsheet_files`. For each valid file id, it asks the Sheets API for spreadsheet details; if that fails with a permission or not-found response allowed for fallback, it creates a smaller record from Drive data. It yields one normalized spreadsheet dictionary with ids, title, URL, creation time, and update time.

**Call relations**: `paginate` uses this as the foundation for every stream, because tabs and cell values both start from knowing which spreadsheets exist. This function delegates file listing to `_iter_spreadsheet_files`, then enriches each file before handing it back upstream.

*Call graph*: calls 1 internal fn (_iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 145–167)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function reads the actual cell grid for each tab in a spreadsheet. It creates one record per tab’s values so a synced sheet can later be recalled as rows of content.

**Data flow**: It receives an HTTP client and a spreadsheet record that already contains tab information. For each tab with a valid title and id, it safely encodes the tab title for a URL, asks the Sheets API for that tab’s rows, and yields a record that includes the cell values plus spreadsheet and tab identifiers.

**Call relations**: `paginate` calls this only for the `sheet_values` stream. It depends on `_spreadsheet_records` having already supplied spreadsheet records with tab metadata, and it hands the finished value records back to `paginate` for batching.

*Call graph*: called by 1 (paginate); 1 external calls (quote).


##### `GoogleSheetsConnector.render`  (lines 169–188)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns raw synced records into readable text. That matters because the rest of the system needs useful human-facing content, not just nested API data.

**Data flow**: It receives a record and the stream it came from. For spreadsheets, it builds text listing tab names; for sheet tabs, it names the parent spreadsheet; for cell values, it converts the grid into plain rows. It returns a title and a formatted text body.

**Call relations**: The sync system calls this when it needs a record represented as recallable content. It uses `_str` to safely pull text fields out of messy API data and `_grid_text` to format cell rows for the `sheet_values` stream. If the stream is not one of this connector’s known streams, it falls back to the parent connector’s rendering behavior.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_sheet_records`  (lines 191–210)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper splits one spreadsheet record into one record per sheet tab. It lets the system treat each tab as its own item instead of hiding all tabs inside the parent spreadsheet.

**Data flow**: It receives a spreadsheet dictionary, reads its spreadsheet id and tab list, skips malformed tab entries, and creates a list of tab records with stable ids, parent spreadsheet information, and the tab title. The result is a flat list of sheet-tab dictionaries.

**Call relations**: `paginate` calls this when the requested stream is `sheets`. It sits between spreadsheet discovery and record batching: `_spreadsheet_records` supplies the parent spreadsheet, `_sheet_records` breaks it apart, and `paginate` yields those tab records.

*Call graph*: called by 1 (paginate).


##### `_grid_text`  (lines 213–218)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This helper converts a Google Sheets cell grid into simple plain text. It makes rows readable by joining cells with ` | `, like a lightweight table.

**Data flow**: It receives a value that may or may not be a list of rows. If it is not a list, it returns an empty string; otherwise it keeps list-shaped rows, turns each cell into text, joins cells across each row, and joins rows with newlines.

**Call relations**: `render` calls this for `sheet_values` records. It is the final formatting step that changes raw cell arrays from the Sheets API into text suitable for recall or display.

*Call graph*: called by 1 (render).


##### `_str`  (lines 221–222)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns optional or messy values into text. It avoids accidentally showing non-text data where a title or label is expected.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `render` uses this when building headings and short descriptions. It keeps rendering predictable even when Google’s API response is missing a field or contains an unexpected type.

*Call graph*: called by 1 (render).


### Microsoft Graph data
Microsoft Teams and Outlook connectors stream collaboration, mail, calendar, contact, and folder data from Microsoft Graph into the sync system.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `source sync`

Microsoft Teams stores many useful conversations, but they are nested: a person belongs to teams, teams contain channels, channels contain messages, and separate chats contain their own messages. This connector is the map that tells the sync system how to walk that structure safely.

It talks to Microsoft Graph, where results often arrive in pages. Think of it like reading a long book one page at a time: the connector follows Microsoft’s “next page” link until it has everything it needs. It first lists the signed-in user’s joined teams, then each team’s channels, then each channel’s messages. It also lists the user’s chats and each chat’s messages.

For message streams, it supports incremental syncing. That means it can use a saved timestamp, called a cursor or watermark, and only keep messages changed after that point. This avoids re-reading the whole history every run.

The connector is careful about permissions. If one team, channel, or chat cannot be opened because access is denied or it disappeared, the connector skips just that parent and keeps going. But if Microsoft refuses the top-level team or chat listing, the stream is marked as skipped instead of crashing the whole run. Message bodies arrive as HTML, so the render step strips tags and turns them into plain readable text.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Microsoft Teams that the signed-in user has joined. Other parts of the connector use this as the starting point for finding channels and channel messages.

**Data flow**: It receives an HTTP client that already knows how to talk to Microsoft Graph. It asks the `/me/joinedTeams` endpoint for teams, follows all result pages, gathers the team records into one list, and returns that list.

**Call relations**: When the connector is asked to sync the `teams` stream, `MicrosoftTeamsConnector.paginate` calls this directly. When the connector needs channels, `MicrosoftTeamsConnector._channels` calls it first so it knows which teams to inspect.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside every joined team. It adds team information to each channel record so later steps know which team the channel came from.

**Data flow**: It starts with an HTTP client, calls `MicrosoftTeamsConnector._teams` to get joined teams, and looks at each team’s id. For every valid team id, it asks Microsoft Graph for that team’s channels, page by page. Before yielding each batch, it attaches context such as the team id and team name. If one team cannot be read because access is denied or it no longer exists, that team is skipped and the rest continue.

**Call relations**: This function sits between teams and channel messages. `MicrosoftTeamsConnector.paginate` calls it when syncing the `channels` stream, and `MicrosoftTeamsConnector._channel_messages` calls it when it needs the list of channels to search for messages.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from every channel the user can access. It can limit the output to messages changed after a saved cursor, which makes repeated syncs much faster.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It gets channel batches from `MicrosoftTeamsConnector._channels`, then uses each channel’s team id and channel id to request messages from Microsoft Graph. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is later than that cursor. For each non-empty batch, it adds context such as team id, channel id, and thread id, then yields the batch. If one channel cannot be read because of a permission or missing-resource problem, it skips that channel and keeps going.

**Call relations**: This is called by `MicrosoftTeamsConnector.paginate` for the `channel_messages` stream. It depends on `MicrosoftTeamsConnector._channels` to discover where messages live, and it uses `with_context` so downstream storage can preserve the message’s place in Teams.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s Microsoft Teams chats. This is the starting list used before reading chat messages.

**Data flow**: It receives an HTTP client, requests `/me/chats` from Microsoft Graph, follows all result pages, collects every chat record into one list, and returns that list.

**Call relations**: When syncing the `chats` stream, `MicrosoftTeamsConnector.paginate` calls this directly. When syncing chat messages, `MicrosoftTeamsConnector._chat_messages` calls it first to know which chats to read.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from the user’s Teams chats. Like channel message syncing, it can skip older messages by using a saved cursor timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It calls `MicrosoftTeamsConnector._chats` to get chats, checks each chat has a usable id, then requests that chat’s messages from Microsoft Graph. If a cursor is supplied, it keeps only messages modified after that cursor. It adds chat id and thread id context before yielding message batches. If one chat cannot be read because it is forbidden or missing, it skips that chat and continues.

**Call relations**: This function is used by `MicrosoftTeamsConnector.paginate` for the `chat_messages` stream. It hands enriched message batches onward so the sync system can store both the message content and the chat it belongs to.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for this connector’s streams. Given a stream name such as teams, channels, or chat messages, it calls the right helper and yields batches of records to the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and routes the request to the matching helper: teams, channels, channel messages, chats, or chat messages. It yields each batch that the helper produces. If Microsoft refuses a top-level request with an authorization-style error, it raises `StreamSkipped`, which tells the sync system to record a skipped stream rather than treat the whole run as broken. If the stream name is unknown, it also raises `StreamSkipped`.

**Call relations**: The wider source-sync framework calls this method when it wants records for one Microsoft Teams stream. This method then calls the more specific helpers and translates broad permission refusals into a controlled skip result.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into a title and readable text. For messages, it removes Microsoft Graph’s HTML wrapping so the saved page contains plain conversation text.

**Data flow**: It receives one record and the stream it came from. For non-message streams, it lets the base connector use its normal rendering. For channel and chat messages, it reads the subject as a safe string, extracts `body.content`, strips HTML tags, builds a simple heading, and returns the title plus the final text body.

**Call relations**: The sync system calls this when it needs a human-readable version of a record. This function relies on `_str` to avoid non-text subjects, `_strip_html` to clean message bodies, and `get_path` to safely reach nested data inside the Microsoft Graph response.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a small piece of HTML text into plain text by removing tags. It is used because Microsoft Graph stores Teams message bodies as HTML even when the useful content is just readable words.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces HTML tags with spaces, trims extra space from the ends, and returns the cleaned text.

**Call relations**: `MicrosoftTeamsConnector.render` calls this while preparing channel and chat messages for display or storage as readable pages.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. This prevents unexpected non-text values from becoming confusing titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject, so the rendered title is always safe text even if Microsoft Graph leaves the subject missing or uses another type.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `source sync runs`

Outlook does not hand over a whole mailbox in one simple response. Microsoft Graph, its web API, gives data in pages and also gives special “delta” links, which are bookmarks meaning “next time, start from here and only tell me what changed.” This file is the Outlook connector that knows how to use those bookmarks safely.

The main class, OutlookConnector, advertises the Outlook streams the system can read. When a sync run asks for one stream, paginate chooses the right path. For most streams it walks Graph delta pages, separates normal records from deleted records, and returns a StreamPage with both the data and the next cursor bookmark. Messages and contacts are trickier because they live inside folders, so their cursor is a small JSON map from folder ID to that folder's delta link.

Conversations are not a real Outlook object here. They are built by reading messages and grouping them by conversationId, like making one folder label for all letters in the same thread.

The file also reshapes raw Graph records into friendlier fields such as email, phone, snippet, start_at, and end_at. If Microsoft refuses access with a permission error, the connector marks the stream as skipped instead of crashing the whole sync.

#### Function details

##### `_strip_html`  (lines 33–36)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a text value so an event description can be stored as readable plain text. If the input is not text, it returns nothing instead of guessing.

**Data flow**: It receives any value. If the value is a string, it replaces anything that looks like an HTML tag with spaces and trims the result; otherwise it returns null. The output is either cleaned text or null.

**Call relations**: OutlookConnector.flatten uses this when it prepares calendar events. That lets event bodies from Microsoft Graph become plain descriptions before they leave this connector.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 39–47)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact. This gives the rest of the system one simple email field even though Microsoft Graph stores addresses as a list of nested objects.

**Data flow**: It receives a contact record. It looks at the record's emailAddresses list, checks each entry for emailAddress.address, and returns the first non-empty string it finds. If there is no usable address, it returns null.

**Call relations**: OutlookConnector.flatten calls this while reshaping contact records. It relies on get_path to safely read a nested field without failing if part of the structure is missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 50–60)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from an Outlook contact. It prefers the mobile phone, then falls back to the first business phone.

**Data flow**: It receives a contact record. It first checks mobilePhone; if that is missing or empty, it scans businessPhones. It returns the first usable phone string or null.

**Call relations**: OutlookConnector.flatten calls this when building the connector's friendlier contact shape. It keeps the choice of phone number in one small helper instead of repeating it inside the flattening code.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate`  (lines 110–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each Outlook stream request to the right reader. It is the connector's front door for fetching pages of contacts, messages, conversations, events, or folders.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching page-producing method, and yields each page back to the sync system. If Microsoft Graph replies with a permission refusal, it turns that into a skipped stream; if the stream is unknown, it also reports it as skipped.

**Call relations**: When the source runner asks OutlookConnector for data, this method decides which specialized method should do the work. It hands conversations to _conversation_pages, messages to _message_delta_pages, contacts to _contact_delta_pages, events to _event_delta_pages, and mail folders to _graph_delta_pages.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages).


##### `OutlookConnector._conversation_pages`  (lines 145–183)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds email conversation records from individual messages. Outlook messages have a conversationId, and this method collapses many messages with the same ID into one thread-like record.

**Data flow**: It receives an HTTP client and an optional cursor. It asks Graph for messages ordered by lastModifiedDateTime, optionally only after the cursor time, groups them by conversationId, keeps the earliest creation time and latest update time, and finally yields a list of conversation records if any were found.

**Call relations**: OutlookConnector.paginate calls this only for the conversations stream. Instead of using a delta endpoint, it derives conversations from message pages and sends the completed conversation list back up to paginate.

*Call graph*: called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 185–222)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta feed and translates it into the system's standard page format. A delta feed is Graph's way of saying “here are the current records, deleted record IDs, and the bookmark for next time.”

**Data flow**: It receives an HTTP client, an initial Graph path, an optional cursor, and optional query parameters. It follows the cursor if one exists, otherwise starts at the initial path. For each Graph response, it separates normal items into records and items marked @removed into deletes, then yields a StreamPage with records, deletions, and the next Graph link as the next cursor.

**Call relations**: This is the shared worker used by several stream-specific readers. OutlookConnector.paginate uses it directly for mail folders, while _message_delta_pages, _contact_delta_pages, and _event_delta_pages wrap it with extra stream-specific setup.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 224–244)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook messages across all mail folders. Because messages belong to folders, it keeps a separate delta bookmark for each folder.

**Data flow**: It receives an HTTP client and a cursor that may contain a JSON map of folder IDs to Graph delta links. It decodes that map, lists the mailbox folders, reads each folder's message delta feed, stamps each returned message with its mail_folder_id, updates that folder's cursor, and yields StreamPage objects whose next cursor is the re-encoded folder map.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It first asks _list_mail_folders for folder IDs, uses _decode_cursor_map and _encode_cursor_map to preserve per-folder progress, and delegates actual Graph delta paging to _graph_delta_pages.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 246–273)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook contacts from the default contact area and any contact folders. Like messages, contacts can live in multiple places, so this keeps a separate bookmark for each place.

**Data flow**: It receives an HTTP client and an optional cursor map. It decodes the cursor, builds a folder list starting with the default contacts area, reads each folder's contact delta feed, updates each folder's stored delta link, and yields StreamPage objects with records, deletions, and the updated encoded cursor. If the default contacts delta endpoint is missing or unsupported, it skips that default part and continues.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It uses _list_contact_folders to discover extra folders, uses _graph_delta_pages to fetch changes, and uses the cursor map helpers to keep progress for every folder.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 275–286)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook calendar events within a broad time window. Microsoft Graph calendar delta needs a date range, so this method supplies one.

**Data flow**: It receives an HTTP client and an optional cursor. It calculates a window from one year in the past to two years in the future, passes that as Graph query parameters when starting fresh, and yields the StreamPage results from the shared delta reader.

**Call relations**: OutlookConnector.paginate calls this for the events stream. This method adds the calendar-specific date range, then hands the actual paging work to _graph_delta_pages.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 288–295)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Gets the IDs of the user's Outlook mail folders. Message syncing needs these IDs because each folder has its own message delta feed.

**Data flow**: It receives an HTTP client. It pages through /me/mailFolders, collects every non-empty folder id it sees, and returns a list of those IDs.

**Call relations**: _message_delta_pages calls this before reading message changes. The folder list tells _message_delta_pages which per-folder delta feeds to walk.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 297–304)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Gets the IDs of the user's Outlook contact folders. Contact syncing needs these IDs so it can read contacts outside the default contact area too.

**Data flow**: It receives an HTTP client. It pages through /me/contactFolders, collects valid folder ids, and returns them as a list.

**Call relations**: _contact_delta_pages calls this before reading folder-based contact changes. The returned IDs become the set of contact folder delta feeds to check.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 306–336)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into a friendlier shape for the rest of the system. It keeps the original fields but adds common, easy-to-query names such as email, phone, snippet, and start_at.

**Data flow**: It receives one raw record and the stream it came from. For contacts, it adds name, email, phone, and created_at fields. For messages, it adds subject, snippet, sender address, sent time, and thread identifiers. For events, it adds title, plain description, start and end times, and location. For other streams, it returns the record unchanged.

**Call relations**: After pages are read, the broader connector framework can call this to normalize each record. It uses _first_email and _phone for contacts, _strip_html for event descriptions, and get_path to safely read nested Microsoft Graph fields.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 339–348)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Turns the stored cursor for folder-based streams back into a usable dictionary. This is needed because messages and contacts track progress separately for each folder.

**Data flow**: It receives a raw cursor string or null. If the cursor is missing, invalid JSON, or not a dictionary, it returns an empty dictionary. Otherwise it returns a dictionary of folder IDs to non-empty cursor strings.

**Call relations**: _message_delta_pages and _contact_delta_pages call this at the start of their work. It turns the saved sync bookmark into the per-folder map those methods can update.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 351–352)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns a per-folder cursor dictionary into the string form that can be saved between sync runs. This preserves each folder's Microsoft Graph delta link.

**Data flow**: It receives a dictionary of folder IDs to cursor strings. If it has entries, it serializes the dictionary as sorted JSON; if it is empty, it returns null. The output becomes the next cursor stored by the sync system.

**Call relations**: _message_delta_pages and _contact_delta_pages call this after updating folder progress. The encoded result is placed on each yielded StreamPage so the next sync can resume in the right place.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).

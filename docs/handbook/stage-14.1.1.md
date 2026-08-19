# Workspace suites, mail, docs, and calendars  `stage-14.1.1`

This stage is a set of read-only “connectors”: adapters that log in to workplace tools and copy out information the system is allowed to see, without changing anything there. It belongs to the behind-the-scenes sync work that feeds the search and recall system.

The Google connectors cover each major surface. Gmail reads mail, turns messages into plain searchable text, and remembers what changed since the last run. Google Calendar reads events and also builds an attendee-focused view, so invitations can be understood by person. Google Docs extracts document text. Google Drive reads files, shared drives, permissions, comments, and revisions. Google Meet turns transcripts and smart notes into meeting pages. Google Sheets converts spreadsheets, tabs, and cell grids into records, while skipping over isolated access problems when possible.

The Microsoft side uses Microsoft Graph, a web doorway into Microsoft 365 data. Teams reads teams, channels, chats, and messages. Outlook reads mail, contacts, calendars, conversations, and folders, while tracking progress so later syncs fetch only new, changed, or deleted items.

## Files in this stage

### Google mail and calendars
Connectors that read Gmail messages and Google Calendar events, preserving searchable text and incremental sync state.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync and record rendering`

Gmail does not store an email as one simple text field. Each message is a nested MIME tree, which means the readable parts may be buried inside separate plain-text or HTML sections, encoded in a web-safe form of base64. This file is the adapter that turns that awkward Gmail shape into records the rest of the system can store and recall.

The main class, GmailConnector, reads the Gmail API. On a first run, it lists messages inside a backfill window, usually recent history, and saves Gmail's history marker so future runs can ask only for changes. On later runs, it asks Gmail for messages added or deleted since that marker. Deleted messages are reported as tombstones, which are small notices saying “remove this item.” If Gmail says the old marker has expired, the connector tells the core system to start over safely.

After it knows which message IDs are new, it fetches each full message, pulls out useful headers such as From, To, Cc, and Subject, decodes the body, and stores a flatter record. Its render method then turns that record into something a human would recognize: a short email-like document with headers and body text. If only HTML is available, a small HTML parser strips tags and keeps readable words and line breaks.

#### Function details

##### `GmailConnector.paginate_source`  (lines 92–102)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector entry point used by the source-sync framework when it wants Gmail records. It passes through the stream, cursor, and backfill date to the Gmail-specific pagination logic.

**Data flow**: It receives an HTTP client, a stream description, the last saved cursor if there is one, and an optional backfill cutoff date. It ignores the current user's ID because Gmail always uses the authenticated mailbox, then returns the async stream of pages produced by GmailConnector.paginate.

**Call relations**: The broader sync runner calls this method through the standard connector interface. Its job is to bridge that generic interface into GmailConnector.paginate, where the real Gmail decision-making happens.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 104–141)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function decides whether a sync run should do an initial Gmail backfill or an incremental change scan. It yields pages of new records and deleted IDs in the shape the core sync system expects.

**Data flow**: It receives a Gmail stream, an HTTP client, and optionally a saved Gmail history cursor. With no cursor, it gathers message IDs from the backfill path; with a cursor, it gathers added and deleted IDs from Gmail history. It then fetches full bodies for added messages in chunks and yields StreamPage objects containing records, deletes, and the next cursor. If Gmail refuses access because the account lacks permission, it turns that into a skipped stream instead of a hard failure.

**Call relations**: GmailConnector.paginate_source hands work to this function. It calls _backfill for first-time reads, _history for later change reads, _fetch_bodies to turn message IDs into full records, and StreamPage to hand results back to the sync engine.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 143–168)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This function lists message IDs for the first sync of a mailbox or stream window. It also chooses the Gmail history marker that future runs should continue from.

**Data flow**: It receives an HTTP client and an optional date floor. First it reads the mailbox profile history ID as a safe starting floor. Then it pages through Gmail's message list, optionally asking only for messages after the given time. It collects message IDs and returns them together with a seed history ID for the next run.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor. It relies on _profile_history_id to get the pre-listing floor and _seed_history_id to choose the cursor that moves the stream out of backfill mode.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 170–173)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This function asks Gmail for the mailbox profile and extracts its current history marker. That marker is used as a safe cursor when a backfill finds no messages.

**Data flow**: It receives an HTTP client, calls Gmail's profile endpoint, reads the historyId field, and returns it if it is a string. If Gmail does not provide a usable value, it returns nothing.

**Call relations**: GmailConnector._backfill calls this before listing messages. Reading it before the list matters because it prevents messages delivered during an empty listing pass from being skipped later.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 175–199)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This function chooses the history cursor that should be saved after an initial backfill. It prefers the newest listed message's history ID, but falls back to the earlier profile marker when needed.

**Data flow**: It receives an HTTP client, the list of message IDs found during backfill, and a fallback floor marker. If there are messages, it fetches the first one in minimal form and tries to read its historyId. If that works, it returns that value; if the message disappeared or no message was listed, it returns the floor.

**Call relations**: GmailConnector._backfill calls this after collecting message IDs. The returned cursor is then passed back through GmailConnector.paginate so the next sync can use Gmail's history endpoint instead of repeating the same backfill.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 201–236)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This function reads Gmail's change log from a saved history ID. It finds which messages were added and which were deleted since the last successful sync.

**Data flow**: It receives an HTTP client and a Gmail history ID. It pages through Gmail history records, extracts message IDs from added and deleted entries, tracks the latest history marker, removes messages that were both added and deleted in the same window from the added set, and returns added IDs, deleted IDs, and the next cursor. If Gmail says the old history ID is too old, it raises CursorExpired so the system can rebuild from scratch.

**Call relations**: GmailConnector.paginate calls this whenever a cursor exists. It uses _message_ids to pull IDs from Gmail's nested history entries, and it signals expired cursors to the core sync layer through CursorExpired.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 238–252)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This function turns Gmail message IDs into full flattened message records. It fetches each message body and skips messages that vanished before they could be fetched.

**Data flow**: It receives an HTTP client and a list of Gmail message IDs. For each ID, it asks Gmail for the full message. Successful responses are passed to _flatten_message and collected. If Gmail returns “not found” for one message, that one is ignored; other errors are allowed to stop the run.

**Call relations**: GmailConnector.paginate calls this after _backfill or _history has identified added messages. It hands each raw Gmail response to _flatten_message so later rendering and storage do not need to understand Gmail's nested API format.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 254–274)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a stored Gmail record into readable prose. It makes synced email look like an email, with From, To, Cc, Subject, and body text, instead of a raw JSON dump.

**Data flow**: It receives a flattened message record and its stream description. For the messages stream, it chooses a title from the subject when possible, formats sender and recipients, chooses the best available body text, and returns both the title and the final text document. For other streams, it falls back to the parent connector's rendering behavior.

**Call relations**: The core system calls render when it needs recallable text for a synced record. This method uses _str, _format_contact, _format_recipients, and _message_body to turn the stored fields into human-readable email prose.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 277–286)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This helper extracts Gmail message IDs from history entries. Gmail wraps each ID inside nested objects, so this keeps that unwrapping in one small place.

**Data flow**: It receives any value that should contain Gmail history message entries. It walks the entries that are dictionaries, looks for a nested message object, reads its id, and returns only non-empty string IDs.

**Call relations**: GmailConnector._history calls this for both added-message and deleted-message sections. It gives _history clean ID lists so that function can focus on combining changes and choosing the next cursor.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 289–316)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This function converts one raw Gmail message response into the simpler record shape used by the sync system. It pulls important headers, addresses, labels, and decoded bodies out of Gmail's nested payload.

**Data flow**: It receives the raw message dictionary from Gmail. It reads selected headers, extracts plain-text and HTML bodies, parses the sender and recipients, keeps string label IDs, decides whether the message is inbound or outbound, and returns a flat dictionary with those fields.

**Call relations**: GmailConnector._fetch_bodies calls this after downloading a full Gmail message. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 319–333)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This helper searches a Gmail MIME payload for the first plain-text body and the first HTML body. MIME is the standard email structure where a message can contain many nested parts.

**Data flow**: It receives the payload dictionary from a Gmail message. It recursively walks the payload tree, decodes the first text/plain and text/html parts it finds, and returns them as a pair of optional strings.

**Call relations**: _flatten_message calls this while building a flat record. Inside it, the nested walk function does the tree traversal and uses _b64url_decode whenever it finds encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 323–330)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This inner function visits one MIME part and then its children, like checking folders inside folders. It is how _extract_bodies finds readable body sections no matter how deeply Gmail nested them.

**Data flow**: It receives one MIME part dictionary. If that part is a plain-text or HTML body with encoded data and that type has not already been found, it decodes and stores it. Then it repeats the same process for each child part.

**Call relations**: _extract_bodies creates and uses this inner walker during body extraction. When it finds encoded text, it hands that text to _b64url_decode so the result becomes normal readable text.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 336–342)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This helper decodes Gmail's body text encoding. Gmail stores message parts as URL-safe base64, which is a text-safe way to carry bytes through an API.

**Data flow**: It receives an encoded string. It adds any missing padding characters, decodes it with URL-safe base64 rules, turns the bytes into UTF-8 text while replacing bad characters, and returns the decoded string. If the input cannot be decoded, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this whenever it finds encoded plain-text or HTML body data. This keeps Gmail's encoding detail away from the rest of the connector.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 345–352)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This helper reads the first email address from a header such as From. It separates the display name from the actual address.

**Data flow**: It receives a header string or nothing. If a header exists, it uses Python's email address parser, takes the first parsed pair, lowercases the address, and returns address plus display name. If nothing usable is found, it returns two empty values.

**Call relations**: _flatten_message calls this for the sender header. It relies on the standard email.utils.getaddresses parser so the connector does not have to manually understand address formats like “Jane Doe <jane@example.com>”.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 355–362)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This helper reads all email addresses from recipient headers such as To or Cc. It returns them in the same small shape the rest of this file expects.

**Data flow**: It receives a header string or nothing. If present, it parses all addresses, lowercases each address, keeps the display name when available, and returns a list of dictionaries with handle and display_name fields.

**Call relations**: _flatten_message calls this for recipient headers. Later, GmailConnector.render uses these parsed recipient lists through _format_recipients to print readable header lines.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 365–370)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This helper formats one person for display in an email header. It prints either just the email address or “Name <address>” when a display name is available.

**Data flow**: It receives a possible email handle and display name. If the handle is not a non-empty string, it returns an empty string. Otherwise it combines the name and address when possible, or returns the address alone.

**Call relations**: GmailConnector.render calls this for the sender. _format_recipients also calls it for each recipient so all contact formatting follows the same rule.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 373–380)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This helper turns a list of parsed recipients into one comma-separated header value. It is used for To and Cc lines in rendered email text.

**Data flow**: It receives a value that should be a list of recipient dictionaries. If it is not a list, it returns an empty string. Otherwise it formats each dictionary with _format_contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this while building the To and Cc header lines. It hands each recipient to _format_contact so one-person and many-person formatting stay consistent.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 383–391)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This helper chooses the best readable body for a rendered email. It prefers plain text, falls back to stripped HTML, and finally uses Gmail's snippet if no full body is available.

**Data flow**: It receives a flattened message record. It first checks body_text and returns it if it has real content. If not, it checks body_html and converts it to text through _HtmlText.extract. If neither body is useful, it returns the trimmed snippet or an empty string.

**Call relations**: GmailConnector.render calls this when assembling the final recallable email document. It uses _HtmlText.extract only when the message has HTML but no usable plain-text body.

*Call graph*: called by 1 (render).


##### `_str`  (lines 394–395)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a maybe-string value into a string for rendering. It prevents non-string data from accidentally becoming visible as confusing text.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this when reading the subject. That lets render treat missing or oddly typed subjects as blank and then fall back to a safe default title.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 402–404)

```
def __init__(self) -> None
```

**Purpose**: This sets up the small HTML-to-text parser used for email bodies. It prepares an empty list where readable text and line breaks will be collected.

**Data flow**: It receives the new parser instance. It initializes the standard HTMLParser base class with automatic character reference conversion, then creates an internal parts list that starts empty.

**Call relations**: _HtmlText.extract creates an instance of this parser before feeding it HTML. The parser methods then fill the parts list as the HTMLParser machinery reads tags and text.


##### `_HtmlText.extract`  (lines 407–412)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This class method converts an HTML email body into readable plain text. It strips tags, keeps text, and preserves useful line breaks around block-like elements.

**Data flow**: It receives raw HTML as a string. It creates a parser, feeds the HTML into it, joins the collected text pieces, cleans extra whitespace on each line, removes blank lines, and returns the final trimmed text.

**Call relations**: _message_body uses this when a message has only an HTML body. During parsing, HTMLParser calls _HtmlText.handle_data, _HtmlText.handle_starttag, and _HtmlText.handle_endtag as it encounters content and tags.


##### `_HtmlText.handle_data`  (lines 414–415)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This parser callback records visible text found inside the HTML. It is the part that keeps the words and sentences from the email body.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that chunk to the parser's internal parts list, changing the parser state but returning no separate value.

**Call relations**: HTMLParser calls this while _HtmlText.extract feeds it an HTML body. The collected chunks are later joined and cleaned by _HtmlText.extract.


##### `_HtmlText.handle_starttag`  (lines 417–419)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This parser callback adds a line break when an opening HTML tag marks a block boundary. It helps paragraphs, list items, and table cells not run together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the block-style tags known to this file, it appends a newline marker to the internal parts list. Attributes are ignored because rendering only needs readable text.

**Call relations**: HTMLParser calls this during _HtmlText.extract. Its newline markers are later cleaned up by _HtmlText.extract into simple readable line breaks.


##### `_HtmlText.handle_endtag`  (lines 421–423)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This parser callback adds a line break when a closing HTML tag ends a block of content. It gives the final plain text a shape closer to the original email.

**Data flow**: It receives the closing tag name. If the tag is one of the known block-style tags, it appends a newline marker to the internal parts list. It returns nothing, but changes what _HtmlText.extract will later join and clean.

**Call relations**: HTMLParser calls this while _HtmlText.extract processes HTML. Together with handle_starttag and handle_data, it produces the raw text pieces that extract turns into final plain text.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `sync run`

This connector is a read-only bridge to Google Calendar. Its job is to ask Google for the user's primary calendar events, remember where it left off, and translate Google's event shape into the simpler records this project stores. Without it, calendar meetings would not appear as recallable content, and attendee relationships would not be available as separate rows.

The file defines two streams. The first, `calendar_events`, stores one record per event. The second, `event_attendees`, stores one record per invited person per event, like taking a meeting invite and making a separate line on a spreadsheet for each attendee.

The connector uses Google's incremental sync system. On the first run, there is no saved cursor, so it looks back 90 days and asks for events, including deleted ones. Google returns a `nextSyncToken`, which is like a bookmark. Later runs send that bookmark so Google only returns changes. If Google says the bookmark is too old, the connector raises a special “cursor expired” signal so the wider system can start fresh. If the user has not granted Calendar access, the stream is skipped rather than treated as a broken run.

For normal events, helper functions flatten nested Google data into plain fields such as title, start time, end time, location, organizer, and attendees. Cancelled events become deletes. The `render` method then turns calendar event records into readable text for search or recall.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main reader for Google Calendar data. It asks Google for pages of events, turns each page into records or deletes, and yields them to the sync engine with an updated cursor when available.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. If there is a cursor, it sends it to Google as a sync token; if not, it asks for events from the last 90 days and includes deleted events. For each Google event it reads the event id, skips malformed items, turns cancelled events into delete markers for the event stream, flattens normal events for `calendar_events`, or expands attendees for `event_attendees`. It outputs `StreamPage` objects containing new or changed records, deleted ids, and eventually the next cursor bookmark.

**Call relations**: The sync framework calls this when it wants records for either Google Calendar stream. Inside the loop it hands raw event data to `_flatten_event` for event records or `_flatten_attendees` for attendee rows. If Google reports an expired sync token, it raises `CursorExpired` so the core system can refetch from scratch; if access is refused because the Calendar permission is missing, it raises `StreamSkipped` so the run records a clean skip.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored calendar event into readable text, so a meeting can be searched or recalled in a human-friendly form. For non-event streams, it falls back to the connector's default rendering behavior.

**Data flow**: It receives a stored record and the stream it belongs to. For `calendar_events`, it reads fields such as title, start and end time, location, attendees, and description, then builds a title plus a multi-line body. Missing or non-text values are ignored rather than shown as confusing placeholders. The result is a pair: the display title and the rendered text body.

**Call relations**: The wider system calls this after records have been synced and need to become readable content. It uses `_str` to safely turn the title into text, then formats the event details itself. If the stream is not `calendar_events`, it hands the work back to the parent `RestConnector`.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Google Calendar event into the simple event record the project stores. It keeps the important meeting details and folds attendee summaries into the event itself.

**Data flow**: It receives one event object from Google's API. It reads nested fields such as start time, end time, organizer, attendees, recurrence id, location, and description. It normalizes attendee emails to lowercase, converts Google time fields into a single timestamp-like string, and outputs one flat dictionary with predictable field names.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for each non-cancelled event in the `calendar_events` stream. While building the record, it calls `_attendee` to simplify each invitee and `_parse_when` to normalize Google’s two different time formats.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes a small, consistent attendee summary for inclusion inside an event record. It keeps the attendee's email handle, display name, and response status.

**Data flow**: It receives one attendee object from Google. It lowercases the attendee email, copies the display name if present, and maps Google's response words, such as `needsAction`, into the project's preferred wording, such as `needs_action`. It outputs a compact dictionary for that attendee.

**Call relations**: `_flatten_event` calls this while building the attendee list embedded in a calendar event record. It does not call other project functions; it only applies the local response mapping.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This turns one calendar event into separate attendee records. It is used when the system wants attendee relationships as their own stream instead of only inside the event.

**Data flow**: It receives one raw Google event. It reads the event id, timestamps, organizer email, and attendee list. For each attendee with a valid email, it creates a row whose id combines the event id and attendee handle, adds the attendee's role, response, display name, and whether the attendee is the calendar owner. It outputs a list of attendee-row dictionaries.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for each non-cancelled event in the `event_attendees` stream. For each attendee row, it asks `_attendee_role` to decide whether the person is the organizer, an optional guest, a required guest, or a resource such as a room.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: This decides what role an attendee has in a meeting. It turns Google's attendee flags into one simple role word.

**Data flow**: It receives one attendee object and a separate yes-or-no value saying whether that attendee matches the organizer. It checks, in order, whether the attendee is the organizer, a resource, optional, or none of those. It outputs one of `organizer`, `resource`, `optional`, or `required`.

**Call relations**: `_flatten_attendees` calls this while creating attendee rows. Its answer becomes the `role` field on each attendee record.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: This normalizes Google Calendar's two event time formats into one string format. It makes timed events and all-day events easier for the rest of the system to treat consistently.

**Data flow**: It receives a value that may be a Google time object. If it contains `dateTime`, it returns that value as text. If it contains an all-day `date`, it turns the date into a midnight UTC timestamp string. If the input is not a usable time object, it returns nothing.

**Call relations**: `_flatten_event` calls this for both the event start and end fields. The normalized values become the stored `starts_at` and `ends_at` fields.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: This is a tiny safety helper for display text. It returns a value only if it is already a string, otherwise it returns an empty string.

**Data flow**: It receives any value. If the value is text, it passes it through unchanged; if not, it replaces it with an empty string. The output is always safe to use where text is expected.

**Call relations**: `GoogleCalendarConnector.render` calls this when preparing the event title. This prevents non-text or missing titles from leaking into the rendered calendar content.

*Call graph*: called by 1 (render).


### Google files and meeting content
Connectors that turn Google Docs, Drive files, Meet artifacts, and Sheets data into searchable synced records.

### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between the project and Google Docs. Its job is to find Google Docs through Google Drive, fetch each document from the Google Docs API, and reshape the result into records the rest of the system can sync and render as readable text.

The flow works like a careful librarian. First it asks Google Drive for files whose type is “Google Doc,” skipping trashed files and sorting them by their last modified time. If the system already synced before, it uses a saved timestamp cursor so Google only returns documents changed since then. Drive may return many results, so the connector follows Google’s page tokens until there are no more pages.

For each Drive file, it then asks the Google Docs API for the full document. If a document is listed in Drive but cannot be opened, for example because permission was removed or the file vanished, the connector keeps a small placeholder record instead of failing the whole sync. But if Drive itself refuses access, the stream is skipped because the account likely lacks the needed permission scope.

Finally, the file includes rendering logic. Google Docs stores text in a nested structure, not as one simple string. The helper walks through paragraph text runs and joins them into the prose a person would expect to read.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main syncing routine for Google Docs records. It gathers changed Google Doc files from Drive, fetches each full document, combines Drive metadata with document content, and yields records in batches so the rest of the sync system can process them.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp from a previous sync. It asks `_iter_doc_files` for Drive file pages, uses each file id to call `_document`, builds a richer record with title, document id, URL, creation time, update time, MIME type, and the original Drive file data, then emits lists of records once the batch reaches the configured size or the run ends. If Google refuses the whole Drive or Docs listing with an authorization-style error, it turns that into `StreamSkipped` so this source can be skipped cleanly instead of crashing the entire run.

**Call relations**: The sync framework calls this method when it wants Google Docs data for the `documents` stream. Inside that larger flow, `paginate` delegates file discovery to `_iter_doc_files` and individual document fetching to `_document`. When access to the stream is broadly refused, it hands a clear skip signal back to the sync framework through `StreamSkipped`.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one complete Google Doc from the Google Docs API. It also protects the sync from failing just because one listed document cannot be opened.

**Data flow**: It takes an HTTP client and a Google Drive file id. It requests the matching document from the Docs API and returns the document data if Google provides it. If Google says the document is forbidden or not found, it returns a tiny fallback record containing only the document id; other errors are allowed to bubble up because they may indicate a real outage or unexpected problem.

**Call relations**: `paginate` calls this once for each Google Doc file it found through Drive. The result is handed back to `paginate`, which merges it with Drive metadata before yielding it to the sync system.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through Google Drive’s file listing and finds Google Docs that should be synced. It supports incremental syncing by asking Drive only for files modified after the saved cursor timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It builds a Drive search query for non-trashed Google Docs, adds a `modifiedTime` filter when a cursor exists, and repeatedly requests pages from Drive. Each response’s `files` value is normalized with `list_or_empty`, yielded when present, and the next page token is followed until Google has no more pages to return.

**Call relations**: `paginate` relies on this method as the first stage of the sync: discover which Drive files are candidates. This method uses `list_or_empty` to avoid surprises when Google’s response has no usable file list, then passes each page of files back to `paginate` for document fetching.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one synced Google Docs record into a readable text document for recall or indexing. It provides both a display title and a body of plain prose.

**Data flow**: It receives a record and its stream description. It reads the title if one is present, asks `_plain_text` to extract paragraph text from the Google Docs body, builds a simple heading that includes the source name and stream name, and returns the title plus the final rendered text.

**Call relations**: After records have been fetched and stored or prepared for indexing, the system can call `render` to convert Google’s nested document format into human-readable text. `render` delegates the tricky body extraction to `_plain_text` and wraps the result with a useful heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts the visible paragraph text from a Google Docs record. It turns Google’s nested document structure into one plain string.

**Data flow**: It receives a document record. It looks for `body.content`, walks through each item that contains a paragraph, then through each paragraph element that contains a text run, collecting the text content in order. It joins all collected text, trims extra whitespace at the ends, and returns the final plain-text body.

**Call relations**: `GoogleDocsConnector.render` calls this helper whenever it needs readable text for a synced document. It focuses only on paragraph text; structures such as tables or section breaks do not add text unless they appear as paragraph text runs in the expected shape.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `sync run`

This connector is the Google Drive “reader” for the project. Its job is to ask Google Drive for a user’s Drive data, page through the results, and pass clean batches back to the main sync engine. Without it, the system would not know how to discover Drive files, notice deleted or trashed files, or collect extra metadata like comments and permissions.

The main stream is files. On the first run, the connector lists all non-trashed files, including files in shared drives, in modification-time order. At the end of that first scan it asks Google for a change token, which is like a bookmark saying “next time, start from here.” Later runs use that bookmark to read only changes: new or updated files are returned as records, while removed or trashed files are returned as deletes. If Google says the bookmark is too old, the connector raises a special “cursor expired” signal so the larger system can do a fresh full sync.

Other streams work differently. Shared drives are fully re-read each run. Permissions, comments, and revisions are fetched by first walking through every file, then asking Google for that file’s child collection. If Drive access is refused because the grant lacks the needed scope, the stream is marked skipped rather than failed. The file also provides a simple renderer that turns a Drive file record into readable text with its name, MIME type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main dispatcher for reading Google Drive streams. Given a stream name, it chooses the right way to fetch that kind of Drive data and yields pages of records or cursor updates to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. For the files stream, it either reads changes from an existing cursor or performs a first full file listing and then saves a new change token. For shared drives, it reads the drives list. For permissions, comments, and revisions, it walks through files and fetches each file’s child records. It outputs batches of records or StreamPage objects, and if Google refuses access with a permission-related status, it turns that into a skipped-stream signal.

**Call relations**: The larger connector framework calls this when it wants data for one Google Drive stream. This function then hands off to _paginate_file_changes, _paginate_files, _start_page_token, _paginate_shared_drives, or _paginate_file_children depending on the stream. It also creates StreamPage objects when it needs to pass along cursor information, and raises StreamSkipped when Drive access is refused.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists Google Drive files in pages. It is used for the first full files sync and also as the starting point for fetching per-file child data such as comments or permissions.

**Data flow**: It receives an HTTP client and an optional modification-time cursor. It builds a Google Drive files query for non-trashed files, optionally limited to files modified after that cursor, then repeatedly asks Google for pages of file records. Each response’s files field is safely converted into a list, and non-empty lists are yielded. It keeps following Google’s next-page token until there are no more pages.

**Call relations**: GoogleDriveConnector.paginate calls this during a first files sync. GoogleDriveConnector._paginate_file_children also calls it so it can discover every file before asking for that file’s permissions, comments, or revisions. It relies on list_or_empty to avoid breaking if Google returns a missing or unexpected list field.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This function asks Google Drive for the current changes bookmark. That bookmark lets the next sync read only what changed after the full file scan.

**Data flow**: It receives an HTTP client, calls Google’s startPageToken endpoint, and reads the startPageToken value from the response. If the token is a non-empty string, it returns it. Otherwise it returns nothing.

**Call relations**: GoogleDriveConnector.paginate calls this after completing the first full files listing. The returned token is wrapped in a StreamPage so the core sync system can store it as the cursor for the next run.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This function reads Google Drive’s change feed from a saved bookmark. It is what makes later syncs efficient, because it asks for only files that changed, disappeared, or were trashed since the last run.

**Data flow**: It receives an HTTP client and a required cursor token. It repeatedly calls Google’s changes endpoint with that token. For each change, it checks the file ID, separates deleted or trashed files into a delete list, and keeps live file objects as records. If Google returns another page token, it yields a StreamPage with records, deletes, and the next cursor. At the end, it uses Google’s new start token as the next saved bookmark. If Google says the old token is expired, it raises CursorExpired instead of returning misleading data.

**Call relations**: GoogleDriveConnector.paginate calls this when syncing the files stream with an existing cursor. This function creates StreamPage objects so the core system can both upsert changed records and tombstone deleted ones. It uses list_or_empty to safely walk the changes list and raises CursorExpired when the sync engine must restart from a fresh full scan.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists the shared drives visible to the grant. Shared drives are re-read as a complete set rather than tracked through the file changes cursor.

**Data flow**: It receives an HTTP client, asks Google’s drives endpoint for a page of shared drives, yields any drive records found, and then follows next-page tokens until Google has no more pages. The output is a series of record lists, each containing shared drive metadata.

**Call relations**: GoogleDriveConnector.paginate calls this for the shared_drives stream. It uses list_or_empty to safely turn the drives field from each API response into a normal list before yielding it to the sync engine.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches per-file collections such as permissions, comments, or revisions. It works by visiting each file first, then asking Google for the selected child data under that file.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. First it calls _paginate_files with no cursor to walk all current files. For each file with a valid ID, it calls the matching child endpoint, follows child-page tokens, and reads the records from the response. If a child stream has a cursor field, it filters out child records that are not newer than the cursor. Before yielding records, it adds the parent file ID and file name so each child record can be traced back to its file. If Google says a particular child collection is forbidden or missing, it skips that file’s child data and continues.

**Call relations**: GoogleDriveConnector.paginate calls this for permissions, comments, and revisions. This function depends on _paginate_files to know which files to inspect, then uses list_or_empty to safely extract each child collection from Google’s responses.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a Google Drive file record into human-readable text for recall or search. For non-file streams, it leaves rendering to the generic parent connector.

**Data flow**: It receives one record and its stream description. If the stream is not files, it passes the work to the base renderer. For files, it reads the name, MIME type, owners, and web link, builds a short text body, and returns a title plus that body. Missing or non-text names are converted to an empty string through _str.

**Call relations**: The sync or indexing layer calls this when it needs a readable version of a record. This function calls _str to safely extract the file title, and otherwise either builds the Google Drive-specific text itself or delegates to the parent class for other streams.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only if it is already text. It prevents non-string values from accidentally becoming titles or text in rendered output.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. If it is anything else, such as null, a number, or a dictionary, it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when reading a file name. It keeps the rendering path simple and safe by making sure the title is always text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `sync and rendering`

This connector is like a librarian for Google Meet history. It asks Google's Meet API for recent conference records, then checks each meeting for generated artifacts: transcripts and AI notes. If a meeting has at least one useful artifact, the connector builds one record for that meeting. That record includes meeting times, transcript text, links to Google Docs, and, when possible, the plain text of Gemini smart notes stored in Google Docs.

The file is careful about timing. Google may create transcripts or notes after a meeting ends, so incremental sync does not start exactly at the last saved time. It looks back one day and rereads a small overlap, so late-arriving files are not missed. At the same time, it still advances the cursor across meetings with no artifacts, so the sync does not get stuck rereading old empty meetings forever.

It is also forgiving about permissions. If Google Meet refuses access with an authorization error, the stream is marked as skipped instead of crashing the whole run. If a linked Google Doc is missing or inaccessible, the connector keeps the document link and continues. Finally, the render step turns the structured API data into a human-readable page with headings, labels, transcript dialogue, and AI summaries.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main reader for the Google Meet stream. It walks through Google Meet conference records page by page, finds meetings with transcripts or smart notes, and yields them to the wider sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that says where the last sync left off. It builds a Google Meet API request, optionally using a one-day lookback from the cursor, then fetches conference pages. For each conference, it asks for the full meeting artifact record, keeps only meetings that actually have transcripts or smart notes, calculates the next cursor from meeting start times, and outputs StreamPage objects. If Google refuses access with a permissions-style error, it turns that into a skipped stream instead of a failed sync.

**Call relations**: This is the entry point used by the source framework when it wants Google Meet data. It relies on _lookback to avoid missing late-generated artifacts, _max_start_time to advance progress, and _conference_record to turn each raw conference into the richer record that will later be rendered.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete record for one Google Meet conference. It gathers all transcript sessions and smart-note sessions belonging to that meeting and packages them with the meeting's basic details.

**Data flow**: It receives one raw conference dictionary from Google. It reads the conference resource name, fetches transcript artifacts and smart-note artifacts underneath that conference, converts each artifact into the connector's own simpler shape, extracts a short conference id, and returns one combined dictionary for the meeting.

**Call relations**: paginate calls this for each conference it sees. This function acts as the middle assembly step: it asks _artifacts for child items, sends transcripts to _transcript, sends smart notes to _smart_note, and hands the completed meeting record back to paginate.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches a list of child artifacts for a conference, such as all transcripts or all smart notes. It hides the repeated page-by-page API work from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the collection name to fetch. If there is no parent name, it returns an empty list. Otherwise it requests each API page, collects the artifact items from the response, follows next-page tokens, and returns the full list.

**Call relations**: _conference_record uses this twice for each meeting: once for transcripts and once for smart notes. By doing the paging here, the higher-level meeting-building code can behave as if Google returned one simple list.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one transcript session from Google's shape into the connector's record shape. It also attaches the individual spoken entries for that transcript.

**Data flow**: It receives a raw transcript dictionary. It extracts stable fields such as id, name, state, start and end times, and Google Docs destination details, then fetches the transcript's entries. It returns a transcript dictionary that is ready to be included inside a meeting record.

**Call relations**: _conference_record calls this for each transcript artifact found under a conference. It delegates the detailed spoken-line fetching to _transcript_entries and uses _docs_destination so the final page can point readers to the durable Google Docs copy.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual lines of a transcript: who spoke, what they said, and when. These entries are what later become the readable dialogue section.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is blank, it returns an empty list. Otherwise it requests transcript-entry pages from Google, converts each entry into a smaller dictionary with participant, text, language, and timing fields, and returns the collected list. If Google reports the entries as missing or inaccessible, it returns whatever it has collected instead of stopping the run.

**Call relations**: _transcript calls this while building each transcript. The rendered page later uses these entries through _dialogue, which turns them into speaker-prefixed lines.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one Gemini smart-note session into the connector's record shape. When possible, it also reads the linked Google Doc and includes its text directly.

**Data flow**: It receives a raw smart-note dictionary. It extracts id, name, state, times, and Google Docs destination fields. If the note points to a Google Docs document, it asks _document_text for the document's plain text and adds that text as the note body when available. It returns the completed smart-note dictionary.

**Call relations**: _conference_record calls this for every smart-note artifact attached to a meeting. It uses _document_text as a best-effort extra step: the note can still be saved with a link even if the document text cannot be read.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a Google Docs document and extracts plain text from it. It is used so smart notes can appear directly in the generated meeting page instead of only as a link.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely encodes the id for a URL, requests the document from the Google Docs API, and passes the returned document structure to _plain_text. It returns the extracted text, or an empty string if the document is missing or access is denied in an expected way.

**Call relations**: _smart_note calls this when a smart note includes a Docs destination. This function bridges from Meet metadata into the Docs API, then hands off the nested document structure to _plain_text for cleanup.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one meeting artifact record into a readable text page. It is the final presentation step that makes API data useful to people and search tools.

**Data flow**: It receives a structured meeting record and the stream description. For the Google Meet meeting-artifacts stream, it builds a title, a short labeled metadata block, a transcript section, and an AI-summary section. It returns the page title and the full text body. For any other stream name, it falls back to the parent connector's rendering behavior.

**Call relations**: After paginate has produced records, the source framework can call render to make displayable prose. This function asks _transcripts_section and _smart_notes_section to format the two major artifact types and uses _labeled and _str for safe, clean text.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcripts for a meeting into a Markdown-style section. It gives readers transcript metadata first, then the actual dialogue.

**Data flow**: It receives a value that should contain transcript records. It treats missing or non-list-like data as empty, then for each transcript builds a small labeled block with state, times, and document link. It turns transcript entries into dialogue text and returns one combined section string, or an empty string if there are no transcripts.

**Call relations**: render calls this while building the final meeting page. It uses _dialogue for the spoken content and _labeled for the small facts that introduce each transcript.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats Gemini smart notes into a Markdown-style AI summaries section. It includes both the note metadata and the extracted note body when available.

**Data flow**: It receives a value that should contain smart-note records. It treats missing data as an empty list, then for each note creates labels for state, times, and document link, followed by the note body text if present. It returns the combined section string, or an empty string when there are no notes.

**Call relations**: render calls this as the AI-summary half of the final page. It depends on earlier work by _smart_note, which may have already filled in the body from Google Docs.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into readable conversation lines. It also joins back-to-back entries from the same speaker so the transcript reads less choppily.

**Data flow**: It receives transcript-entry data. For each entry, it reads the text and skips blank lines. It finds a readable speaker name, then either appends the text to the previous line if the same person was already speaking, or starts a new 'Speaker: text' line. It returns the dialogue as newline-separated text.

**Call relations**: _transcripts_section calls this when it needs the human conversation part of a transcript. It uses _speaker to choose a display name and _str to avoid treating non-text values as text.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts simple text from the nested structure returned by the Google Docs API. It removes the document machinery and keeps only the words people care about.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the body content, walks through paragraph elements, collects text-run content, joins all text chunks together, trims the result, and returns a plain string.

**Call relations**: _document_text calls this after downloading a smart-notes Google Doc. This helper is the cleanup stage that changes a structured Docs response into text that can be placed in the rendered meeting page.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls the Google Docs destination fields out of a transcript or smart-note record. Those fields give readers a durable document id and export link for the artifact.

**Data flow**: It receives a raw artifact dictionary. If the artifact has a docsDestination object, it extracts the document id and export URL as strings. If not, it returns an empty dictionary. The returned fields can be merged into transcript or smart-note records.

**Call relations**: _transcript and _smart_note both call this while converting Google artifact data. It keeps Docs-link extraction in one place so both artifact types are treated consistently.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest meeting start time from a batch of conferences and the existing cursor. The result tells the next sync where it can safely resume.

**Data flow**: It receives a list of conference dictionaries and the current cursor value. It scans each conference's startTime field and keeps the greatest timestamp string it sees. It returns that newest value, or the original cursor if nothing newer appears.

**Call relations**: paginate calls this after fetching a page of conference records. Its result becomes the next cursor sent out with the StreamPage, allowing later syncs to continue from the newest known meeting.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a cursor time backward by one day. It exists because Google may create transcripts or smart notes after a meeting is over, so the connector intentionally rereads a small recent window.

**Data flow**: It receives an ISO timestamp string, converts it into a datetime value, subtracts the configured one-day lookback, and formats it back into Google's expected timestamp style with a trailing Z for UTC time. It returns that adjusted timestamp string.

**Call relations**: paginate calls this when it builds the API filter for incremental sync. The returned time helps the connector refetch recent meetings without starting over from the beginning.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the short id from a Google resource name. For example, from a slash-separated path, it keeps only the final piece.

**Data flow**: It receives a resource name string. If the string is not empty, it splits on the last slash and returns the last segment. If the input is empty, it returns an empty string.

**Call relations**: Several conversion helpers call this while turning Google API resource names into stable, compact ids. _speaker also uses it to make participant names easier to read.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses the speaker label used in transcript dialogue. It gives a fallback label when Google does not provide a usable participant name.

**Data flow**: It receives a participant value from a transcript entry. It first keeps it only if it is text, then extracts the final resource-id segment. If that produces nothing, it returns the generic label 'Participant'.

**Call relations**: _dialogue calls this for each transcript entry. The returned label becomes the name before the colon in each rendered conversation line.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only when it is already a string. It prevents unexpected non-text values from leaking into rendered pages.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: Many functions use this while reading Google API dictionaries, because API fields may be missing or shaped differently than expected. It keeps the rest of the formatting code simple and defensive.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats a list of label-and-value pairs into simple 'label: value' lines. It skips blank values so the final page does not contain empty labels.

**Data flow**: It receives pairs such as ('start', '2024-...'). For each pair with a non-empty value, it creates one text line. It joins those lines with newline characters and returns the result.

**Call relations**: render, _transcripts_section, and _smart_notes_section use this to build clean metadata blocks. It is the common formatter for the small facts shown above transcripts and AI summaries.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `during source sync pagination and record rendering`

This connector is the bridge between the project and Google Sheets. It first asks Google Drive for spreadsheet files, because Drive knows which spreadsheets exist and when each one was last changed. Then, for each spreadsheet, it asks the Google Sheets API for the spreadsheet title, its tabs, and, when needed, the rows of each tab.

The file produces three kinds of records: one for each spreadsheet, one for each tab inside a spreadsheet, and one for the cell values in each tab. It syncs incrementally, meaning it remembers the latest Drive modified time it has reached and asks Google only for files changed at or after that time next time. The comparison is intentionally inclusive, so if several files share the same timestamp, a later run may re-read some of them rather than accidentally miss one.

A major job of this file is deciding what to do when Google refuses a request. If the whole grant lacks permission, the stream is skipped. If only one spreadsheet or tab is refused, the connector records that file for retry later and keeps moving. This is like a librarian marking one locked cabinet for later while still shelving the books they can reach. It also formats synced records into readable text for recall.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 162–212)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for a Google Sheets stream. It walks through spreadsheets changed since the saved cursor, turns them into the requested kind of records, and yields pages of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. It decodes the cursor into a last-seen modified time plus any previously refused file IDs, lists spreadsheet files from Drive, gathers records for each file, updates the cursor watermark, and yields pages. It also retries carried refused files one at a time and updates the cursor so future runs know what has been settled.

**Call relations**: The sync framework calls this when it needs records. It relies on _decode_cursor at the start, _spreadsheet_visits for the normal Drive listing, _visit_records to make stream-specific records, _carried_visit for old refusals, _settled to update the retry set, and _encode_cursor whenever it reports progress. If Google refuses the whole grant rather than a single file, it raises StreamSkipped so the run records a skip instead of pretending the stream was empty.

*Call graph*: calls 9 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _error_detail, _is_quota_refusal, _settled); 1 external calls (__init__).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 214–239)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function pages through Google Drive’s file list and yields batches of spreadsheet files. It limits the search to untrashed Google Sheets and optionally only those modified at or after a saved time.

**Data flow**: It receives an HTTP client and an optional watermark. It builds a Drive query, follows Drive page tokens, cleans the returned file list into a safe list, and yields each non-empty batch of file metadata. It stops when Drive has no next page token.

**Call relations**: _spreadsheet_visits calls this as the first step of normal syncing. This function only lists Drive files; later functions fetch Sheets-specific details for each file.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 241–249)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This turns Drive file listings into individual spreadsheet visits. A visit is the connector’s internal note saying, “here is this file’s metadata, here is its Sheets metadata if available, and here is whether access was refused.”

**Data flow**: It receives an HTTP client and a watermark, then reads batches from _iter_spreadsheet_files. For each file with a usable ID, it asks _file_visit to fetch and combine Drive and Sheets information, then yields that visit onward.

**Call relations**: paginate uses this during the normal Drive-listing part of a sync. It sits between the raw Drive listing and the record-making step, handing each prepared file visit to _visit_records.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 251–265)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This retries a spreadsheet ID that was refused in an earlier run. It checks whether the file now exists, is trashed, is still refused, or can finally be read.

**Data flow**: It receives an HTTP client and a file ID from the carried retry list. It asks Drive for that one file’s metadata, including whether it is trashed. If the file is gone or access is still refused, it returns a visit with no record and the correct refusal status. If the file is readable and not trashed, it passes the file to _file_visit to build a normal visit.

**Call relations**: paginate calls this after finishing the normal Drive listing, but only for carried IDs that were not already seen in the listing. It uses _error_detail and _is_per_file_refusal to decide whether an error is about this one file or should be raised as a bigger failure.

*Call graph*: calls 3 internal fn (_file_visit, _error_detail, _is_per_file_refusal); called by 1 (paginate); 1 external calls (__init__).


##### `GoogleSheetsConnector._file_visit`  (lines 267–294)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This combines Drive metadata with Google Sheets metadata for one spreadsheet. If the Sheets API refuses only this file, it still returns a usable fallback record based on Drive information.

**Data flow**: It receives an HTTP client, a file ID, and Drive’s file metadata. It asks the Sheets API for spreadsheet details such as title and tabs. If that succeeds, it builds one combined record with IDs, title, URL, creation time, and modified time. If the file alone is refused, it marks the visit as refused and builds a smaller record from the Drive name instead.

**Call relations**: _spreadsheet_visits calls this for files found during normal listing, and _carried_visit calls it for retried files. It uses _error_detail and _is_per_file_refusal to separate a single-file problem from a broader permission or quota problem.

*Call graph*: calls 2 internal fn (_error_detail, _is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 1 external calls (__init__).


##### `GoogleSheetsConnector._visit_records`  (lines 296–312)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This chooses how to turn one spreadsheet visit into records for the requested stream. The same visited spreadsheet can become a spreadsheet record, several tab records, or several cell-grid records.

**Data flow**: It receives an HTTP client, the stream being synced, and a file visit. If the visit has no record, it returns no records and preserves the refusal flag. For the spreadsheets stream it returns the spreadsheet itself; for sheets it calls _sheet_records; for sheet_values it calls _sheet_value_records and combines file-level and tab-level refusal information.

**Call relations**: paginate calls this after each file visit is prepared. It dispatches to _sheet_records for tab summaries or _sheet_value_records for actual cell values, then hands the resulting records back to paginate for paging and cursor updates.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 314–370)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This reads the rows from each tab in a spreadsheet. It uses batch requests for efficiency, but falls back to one-tab-at-a-time reads if Google refuses a batch so it can find which tab failed.

**Data flow**: It receives an HTTP client and a spreadsheet record that includes tab information. It extracts valid tab titles and IDs, groups them into batches, asks the Sheets values API for rows, checks that Google returned the same number of ranges requested, and turns each result into a record. If a batch is refused for a per-file reason, it retries each tab individually and skips only the refused tabs.

**Call relations**: _visit_records calls this for the sheet_values stream. It uses _quoted_sheet_range so tab names are interpreted correctly by Google, _sheet_value_record to build each output record, _error_detail and _is_per_file_refusal to classify failures, and StreamFault if Google’s batch response shape does not match the request.

*Call graph*: calls 5 internal fn (__init__, _error_detail, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 2 external calls (list_or_empty, quote).


##### `GoogleSheetsConnector.render`  (lines 372–391)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Sheets record into readable text for recall or display. It gives each record a clear heading and a body that makes sense for its stream.

**Data flow**: It receives one record and its stream description. For spreadsheet records, it lists tab names; for sheet records, it names the parent spreadsheet; for sheet value records, it turns rows into plain text with cells separated by vertical bars. It returns a short title and a longer rendered text block.

**Call relations**: The broader source system calls this when it needs human-readable content from records. It uses _str to safely read string fields and _grid_text to format tab rows.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 394–407)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This reads the saved sync position for the connector. It supports both old simple cursors that are just a timestamp and newer JSON cursors that also remember refused files to retry.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns no watermark and no carried files. If the cursor is plain text or not a JSON object, it treats it as the watermark. If it is a valid checkpoint object, it returns the watermark, refused file IDs, and the last retried ID.

**Call relations**: paginate calls this at the start of a run. Its output shapes the rest of the sync: where Drive listing begins and which refused files are retried after the listing.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 410–415)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This writes the connector’s current progress into a cursor string. It only uses the more detailed checkpoint format when there are refused files that need to be carried into later runs.

**Data flow**: It receives a watermark, a set of refused file IDs, and an optional retried marker. If there is no watermark or no refused files, it returns the simple watermark. Otherwise, it creates a JSON checkpoint with the watermark, a sorted limited list of refused IDs, and the retry marker when present.

**Call relations**: paginate calls this whenever it yields a page or final checkpoint. The cursor it creates is later read by _decode_cursor in the next run.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 418–419)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This updates the set of refused file IDs after the connector tries a file. It either keeps/adds the file if it is still refused or removes it if access is now settled.

**Data flow**: It receives the current refused set, one file ID, and a yes-or-no flag saying whether the file is still refused. It returns a new set with that file included or removed as appropriate.

**Call relations**: paginate calls this after normal file visits and carried retries. Its result feeds into _encode_cursor so future runs retry only the files that still need attention.

*Call graph*: called by 1 (paginate).


##### `_error_detail`  (lines 422–427)

```
def _error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This extracts the useful Google API error object from an HTTP error response. It gives the rest of the file a safe dictionary to inspect, even if the response body is missing or not valid JSON.

**Data flow**: It receives an HTTP status error. It tries to parse the response body as JSON, then returns the nested error object if it is a dictionary. If parsing fails or the shape is not right, it returns an empty dictionary.

**Call relations**: paginate, _carried_visit, _file_visit, and _sheet_value_records call this before deciding what kind of refusal happened. Its output is passed to _is_quota_refusal or _is_per_file_refusal.

*Call graph*: called by 4 (_carried_visit, _file_visit, _sheet_value_records, paginate); 1 external calls (dict_or_empty).


##### `_is_per_file_refusal`  (lines 430–436)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This decides whether a Google error means only one file or tab could not be read. That distinction matters because a single-file refusal can be skipped and retried without stopping the whole stream.

**Data flow**: It receives an HTTP status code and parsed error details. It returns true only for relevant 403 or 404 responses that have a Google error body and are not quota problems and not grant-wide permission problems.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this when a Google request fails. It calls _is_quota_refusal and _is_grant_refusal to rule out broader failures before allowing a local fallback.

*Call graph*: calls 2 internal fn (_is_grant_refusal, _is_quota_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records).


##### `_is_quota_refusal`  (lines 439–442)

```
def _is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects errors caused by Google quota or rate limits. These are not treated as missing permission, because retrying later may be needed and silently skipping would hide a real run failure.

**Data flow**: It receives parsed Google error details. It checks the top-level status and the listed error reasons for known quota and rate-limit names, then returns true or false.

**Call relations**: paginate uses this before converting broad 401 or 403 errors into StreamSkipped. _is_per_file_refusal also uses it to make sure quota failures are not mistaken for one unreadable file.

*Call graph*: called by 2 (paginate, _is_per_file_refusal); 1 external calls (list_or_empty).


##### `_is_grant_refusal`  (lines 445–450)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects errors that mean the connected account or project lacks the needed Drive or Sheets access. In plain terms, the connector is not allowed to use the service at all or is missing the right scope.

**Data flow**: It receives parsed Google error details. It checks Google’s error reasons and details for known grant-wide signals, then returns true if the problem is about the overall permission grant rather than one file.

**Call relations**: _is_per_file_refusal calls this while classifying errors. If this returns true, the error is not handled as a per-file fallback and is allowed to become a stream-level skip or failure.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 453–474)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This converts one spreadsheet record into one record per tab. Each tab record keeps enough parent spreadsheet information to be useful on its own.

**Data flow**: It receives a spreadsheet dictionary that includes a spreadsheet ID and possibly a list of sheets. It loops through valid sheet entries, reads each tab’s properties and sheet ID, and returns records with combined IDs, parent title, tab title, and inherited timestamps.

**Call relations**: _visit_records calls this when the requested stream is sheets. The returned tab records are then paged by paginate like any other stream records.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 477–490)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the final record for the cell values of one tab. It attaches parent spreadsheet and tab identity to the values returned by Google.

**Data flow**: It receives the parent spreadsheet, the tab title, the tab ID, and Google’s value range response. It copies the value range fields and adds a stable record ID, spreadsheet ID and title, sheet ID and title, and inherited timestamps.

**Call relations**: _sheet_value_records calls this after each successful batch or individual tab value read. The finished records flow back through _visit_records to paginate.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 493–495)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This formats a sheet tab title so Google reads it as a tab name in A1 notation, the spreadsheet range language. It also escapes apostrophes inside names so titles like Bob's Sheet still work.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title and wraps the whole title in single quotes. It returns the quoted range name used in values API requests.

**Call relations**: _sheet_value_records calls this when requesting tab values. Correct quoting prevents Google from confusing a tab name with a cell reference or named range.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 498–503)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This turns a grid of cell values into simple readable text. It is used so a synced sheet tab can be searched or displayed as rows rather than raw JSON.

**Data flow**: It receives any value. If the value is not a list, it returns an empty string. If it is a list of row lists, it joins cells in each row with ` | ` and joins rows with newlines.

**Call relations**: render calls this for sheet_values records. The formatted text becomes the body of the rendered recall content.

*Call graph*: called by 1 (render).


##### `_str`  (lines 506–507)

```
def _str(value: Any) -> str
```

**Purpose**: This safely returns a value only if it is already a string. It avoids accidentally rendering numbers, objects, or missing fields as misleading text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render calls this while building headings and short bodies. It keeps rendered output tidy when Google records are missing optional fields.

*Call graph*: called by 1 (render).


### Microsoft Graph collaboration
Connectors that read Microsoft Teams and Outlook surfaces through Microsoft Graph for searchable collaboration, mail, contact, and calendar records.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `during source sync`

Microsoft Teams keeps conversations in several nested places: a user belongs to teams, teams contain channels, channels contain messages, and the user also has separate chats with their own messages. This connector is the map that tells the sync system how to walk through that structure without getting lost.

It talks to Microsoft Graph, which returns results in pages. Like reading a long book one page at a time, the connector follows each "next page" link until a collection is finished. It first asks for the signed-in user's joined teams, then asks each team for its channels, then asks each channel for its messages. It also asks for the user's chats and each chat's messages.

Message streams are incremental. That means the connector can use a saved timestamp, called a cursor or watermark, to fetch only messages changed after the last sync. This keeps later runs from rereading everything.

The file is careful about partial failure. If one team, channel, or chat is forbidden or missing, it skips that parent and continues with the rest. But if Microsoft refuses the top-level request because the login grant lacks permission, the connector marks that stream as skipped instead of crashing the whole run. For readable output, message bodies stored as HTML are stripped down to plain text.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Microsoft Teams that the signed-in user has joined. Other parts of the connector need this list before they can find channels and channel messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/joinedTeams`, follows all result pages, collects every team record into one list, and returns that list.

**Call relations**: This is the starting point for the team side of the sync. `MicrosoftTeamsConnector.paginate` calls it when the requested stream is teams, and `MicrosoftTeamsConnector._channels` calls it first so it knows which teams to inspect for channels.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside each team the user has joined. It also adds team information to each channel record so later records still know which team they came from.

**Data flow**: It receives an authenticated HTTP client. It first gets teams from `MicrosoftTeamsConnector._teams`, then for each valid team ID asks Microsoft Graph for that team's channels. Before yielding a page of channels, it uses `with_context` to attach the team ID and team name. If a single team cannot be read because it is forbidden or missing, it skips that team and continues.

**Call relations**: This sits between teams and channel messages. `MicrosoftTeamsConnector.paginate` uses it when syncing the channels stream, and `MicrosoftTeamsConnector._channel_messages` uses it so it can visit each channel and fetch its messages.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every channel the connector can reach. It can limit results to messages changed after the last saved sync point.

**Data flow**: It receives an authenticated HTTP client and an optional cursor timestamp. It gets channel pages from `MicrosoftTeamsConnector._channels`, reads each channel's team ID and channel ID, then asks Microsoft Graph for that channel's messages. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is newer. It yields non-empty message pages with added context such as team ID, channel ID, and thread ID.

**Call relations**: This is called by `MicrosoftTeamsConnector.paginate` when the system asks for the `channel_messages` stream. It depends on `MicrosoftTeamsConnector._channels` for the list of places to visit, and uses `with_context` so downstream storage can tie each message back to its channel and team.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the user's Microsoft Teams chats, separate from team channels. This gives the connector the chat IDs it needs before it can read chat messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/chats`, follows all pages of results, collects all chat records into a list, and returns that list.

**Call relations**: This begins the chat side of the sync. `MicrosoftTeamsConnector.paginate` calls it for the chats stream, and `MicrosoftTeamsConnector._chat_messages` calls it first so it knows which chats to read messages from.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from each one-to-one or group chat available to the signed-in user. It supports incremental syncing so repeated runs can focus on newly changed messages.

**Data flow**: It receives an authenticated HTTP client and an optional cursor timestamp. It gets the chat list from `MicrosoftTeamsConnector._chats`, skips records without a usable chat ID, then asks Microsoft Graph for messages in each chat. If a cursor is present, it keeps only messages modified after that cursor. It yields non-empty pages and adds the chat ID and thread ID to each message record.

**Call relations**: This is used by `MicrosoftTeamsConnector.paginate` for the `chat_messages` stream. It builds on `MicrosoftTeamsConnector._chats` and passes enriched message records onward through `with_context`, so later processing can tell which chat each message belongs to.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the connector's traffic director for reading Microsoft Teams streams. Given a requested stream, it chooses the right helper to fetch teams, channels, channel messages, chats, or chat messages.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields pages of records. If Microsoft Graph refuses access at the top level with an authorization-style error, it raises `StreamSkipped` so the sync records a skipped stream rather than treating it as a broken connector. If the stream name is unknown, it also reports it as skipped.

**Call relations**: The broader sync framework calls this when it wants records for one stream. This method then hands the work to `MicrosoftTeamsConnector._teams`, `MicrosoftTeamsConnector._channels`, `MicrosoftTeamsConnector._channel_messages`, `MicrosoftTeamsConnector._chats`, or `MicrosoftTeamsConnector._chat_messages`, depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Teams record into a title and readable text for storage or search. For message records, it extracts the message subject and cleans the HTML body into plain text.

**Data flow**: It receives one record and the stream it came from. For teams, channels, and chats, it delegates to the base connector's default rendering. For channel and chat messages, it reads the subject with `_str`, reads `body.content` through `get_path`, removes HTML tags with `_strip_html`, builds a simple heading, and returns the title plus readable message text.

**Call relations**: The sync system calls this after records are fetched and before they are stored as recallable pages. It uses `_str` and `_strip_html` as small cleanup helpers so Microsoft Graph's raw message format becomes easier for people to read.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes HTML tags from a message body so the stored text is more readable. Microsoft Graph stores Teams message content as HTML, which is useful for formatting but noisy for plain search text.

**Data flow**: It receives any value. If the value is not a string, it returns `None`. If it is a string, it replaces HTML tags with spaces, trims extra space from the ends, and returns the cleaned text.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when rendering channel or chat messages. It is a small cleanup step between reading the raw `body.content` field and producing the final human-readable page text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts an optional value into a usable string for titles. It avoids accidentally using non-text values as message subjects.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. It keeps the rendering path simple by ensuring the title is always text, even when Microsoft Graph omits the subject or provides something unexpected.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `source sync`

This connector is the bridge between the project and a user's Outlook mailbox. Without it, Outlook data would not enter the system, and each run would either miss changes or have to reread the whole mailbox from scratch.

It talks to Microsoft Graph, Microsoft's web API for Outlook and other Microsoft 365 data. For most streams it uses Graph's “delta” feeds: these are like a bookmark in a long notebook. The first sync walks through all available pages and saves the final delta link. The next sync starts from that saved link, and Graph returns only what changed, including deleted records marked as removals.

Messages and contacts are more complicated because they can live in folders. This file stores one saved Graph cursor per folder as a small JSON map. Calendar events use a moving time window around the current date, so the connector does not try to sync an unlimited calendar history.

Conversations are not a separate Graph object here. The connector builds them by reading messages and grouping them by conversation ID, keeping one summary record per email thread. The file also reshapes raw Outlook records into friendlier fields, such as contact email, message sender, event start time, and plain-text event description.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: Turns a Python date-and-time value into the exact UTC timestamp format Microsoft Graph expects in filters. This is used when the connector asks Graph for items after a certain time.

**Data flow**: It receives a datetime value, converts it to UTC, formats it as text like `2024-01-01T12:00:00Z`, and returns that string. It does not change any outside state.

**Call relations**: The conversation and message sync helpers call this when they need to apply a starting time limit. It prepares the timestamp before those helpers send requests to Microsoft Graph.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from text, mainly so calendar event descriptions become easier to read. If the input is not text, it safely returns nothing.

**Data flow**: It receives any value. If the value is a string, it replaces tags such as `<p>` or `<br>` with spaces, trims the result, and returns the cleaned text. If the value is not a string, it returns `None`.

**Call relations**: The `flatten` method uses this while reshaping event records. It turns Graph's HTML-like event body into a plain description field for downstream use.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact record. This gives the system a simple `email` field even though Graph stores contact emails as a list of nested objects.

**Data flow**: It receives one contact record, looks at its `emailAddresses` list, checks each entry for `emailAddress.address`, and returns the first non-empty address string it finds. If there is no usable address, it returns `None`.

**Call relations**: The `flatten` method calls this when preparing contact records. It relies on the shared `get_path` helper to safely read a nested field without crashing if pieces are missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from an Outlook contact record. It prefers the mobile number, then falls back to the first business phone number.

**Data flow**: It receives one contact record, first checks `mobilePhone`, then checks the `businessPhones` list. It returns the first non-empty phone string it finds, or `None` if there is no phone number.

**Call relations**: The `flatten` method calls this while turning Graph contact data into simpler contact fields. It is a small helper that keeps the contact-cleanup rules in one place.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the connector entry point used by the source runner when it wants pages of Outlook data. It passes through the stream, saved cursor, and optional backfill start time to the real pagination logic.

**Data flow**: It receives an HTTP client, a stream description, an optional saved cursor, the current user ID, and an optional backfill date. It ignores the user ID here, forwards the other relevant values to `paginate`, and yields whatever pages `paginate` produces.

**Call relations**: The broader source framework calls this method during a sync. It immediately hands control to `OutlookConnector.paginate`, which chooses the correct Outlook-specific read path.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses how to read each Outlook stream and turns permission failures into a clean “skipped stream” result. This is the main traffic director for contacts, messages, conversations, events, and mail folders.

**Data flow**: It receives an HTTP client, a stream description, an optional cursor, and an optional backfill date. Based on the stream name, it calls the matching helper and yields its pages. If Microsoft Graph returns 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` so the sync records a skip instead of a hard failure.

**Call relations**: `paginate_source` calls this during syncing. This method then delegates to `_conversation_pages`, `_message_delta_pages`, `_contact_delta_pages`, `_event_delta_pages`, or `_graph_delta_pages`; if the stream is unknown, it reports that the stream is not implemented.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records by reading messages and grouping them into email threads. Microsoft Graph is not used here as a conversation source directly; the connector derives conversations from message data.

**Data flow**: It receives an HTTP client, an optional cursor, and an optional starting date. It asks Graph for messages ordered by last modified time, optionally filtered after the cursor or backfill date. It groups messages by `conversationId`, keeps the earliest creation time and latest update per thread, and yields one list of conversation summary records.

**Call relations**: `paginate` calls this for the `conversations` stream. It uses `_graph_instant` when it needs to format a backfill date for Graph, and it reads message pages through the connector's OData page-fetching helper inherited from the base REST connector.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta feed and turns Graph's response shape into the project's standard page shape. Delta feeds are Graph's way of saying “here are the changes since your last bookmark.”

**Data flow**: It receives an HTTP client, an initial Graph path, an optional saved cursor, and optional query parameters. It follows Graph pages, separates normal records from deleted items marked with `@removed`, captures the next or final delta link as the next cursor, and yields `StreamPage` objects containing records, delete IDs, and the cursor to save.

**Call relations**: This is the shared low-level delta reader. `paginate` uses it directly for mail folders, while message, contact, and event helpers call it to avoid repeating the same next-link and delete-handling logic.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs messages folder by folder, because Outlook mail is organized into folders and each folder has its own delta bookmark. It also tags each message with the folder it came from.

**Data flow**: It receives an HTTP client, an optional JSON cursor map, and an optional backfill date. It decodes the cursor map, lists mail folders, builds a Graph delta request for each folder, optionally adds a first-run received-date filter, and yields `StreamPage` objects. As each folder produces a new cursor, it re-encodes the updated folder-to-cursor map for saving.

**Call relations**: `paginate` calls this for the `messages` stream. It asks `_list_mail_folders` for folder IDs, uses `_graph_delta_pages` to read each folder's changes, and uses `_decode_cursor_map`, `_encode_cursor_map`, `_graph_instant`, and URL quoting to keep Graph paths and saved state safe.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs contacts from the default contact area and any additional contact folders. Like messages, it keeps a separate delta bookmark for each folder.

**Data flow**: It receives an HTTP client and an optional JSON cursor map. It decodes saved folder cursors, lists contact folders, reads delta pages for the default contacts and each folder, updates the cursor map as new delta links arrive, and yields `StreamPage` objects with records, deletes, and the updated saved cursor.

**Call relations**: `paginate` calls this for the `contacts` stream. It uses `_list_contact_folders` to discover folders and `_graph_delta_pages` to read each folder. If the default contacts endpoint is missing or rejected with certain not-found style errors, it skips that default area and continues.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs calendar events through Graph's calendar-view delta endpoint. It limits the first request to a practical window around today instead of asking for all calendar history and far-future events.

**Data flow**: It receives an HTTP client and an optional cursor. It calculates a start time one year in the past and an end time two years in the future, sends those as parameters on the initial delta request, and yields the pages produced by `_graph_delta_pages`.

**Call relations**: `paginate` calls this for the `events` stream. It delegates the actual delta paging, deletion detection, and cursor extraction to `_graph_delta_pages`.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user's Outlook mail folders. Message syncing needs this list because each folder is read separately.

**Data flow**: It receives an HTTP client, requests mail folder pages from Graph, scans each folder record for a non-empty `id`, and returns a list of folder ID strings.

**Call relations**: `_message_delta_pages` calls this before syncing messages. The returned folder IDs become the set of per-folder delta feeds that message syncing walks through.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user's extra Outlook contact folders. Contact syncing uses this so it can read contacts outside the default contact list.

**Data flow**: It receives an HTTP client, requests contact folder pages from Graph, extracts non-empty folder IDs, and returns them as a list.

**Call relations**: `_contact_delta_pages` calls this before reading contacts. The helper supplies the folder IDs that are added alongside the default contacts location.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into records with simpler, more consistent field names. This makes Outlook data easier for the rest of the system to search, display, or store.

**Data flow**: It receives one raw record and the stream it belongs to. For contacts, it adds fields like name, email, phone, and created time. For messages, it adds subject, snippet, sender, sent time, and thread IDs. For events, it adds title, cleaned description, start and end times, and location. Other streams pass through unchanged.

**Call relations**: The source framework uses this after records have been fetched. It calls `_first_email`, `_phone`, `_strip_html`, and the shared `get_path` helper to safely pull useful values from Graph's nested record structure.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Reads the saved per-folder cursor map from JSON text. If the saved value is missing, broken, or not the expected shape, it safely returns an empty map.

**Data flow**: It receives optional text. If there is no text, it returns `{}`. Otherwise it tries to parse JSON, checks that the result is a dictionary, keeps only non-empty string cursor values, and returns a clean dictionary of folder ID to cursor.

**Call relations**: Message and contact syncing call this at the start of their folder-by-folder sync. It turns the runner's saved cursor string back into the map those helpers need.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns the per-folder cursor map back into JSON text so the sync runner can save it for the next run. Empty maps are stored as no cursor.

**Data flow**: It receives a dictionary from folder ID to cursor string. If the dictionary has entries, it serializes it to sorted JSON text; if it is empty, it returns `None`.

**Call relations**: Message and contact syncing call this after receiving new folder delta links. The encoded value is placed on each yielded `StreamPage` so the broader sync system can remember where to resume.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).

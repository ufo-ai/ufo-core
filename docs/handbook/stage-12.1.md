# Communications, calendar, and meeting source connectors  `stage-12.1`

This stage is the set of read-only “connectors” that bring communication and meeting data into the system. It sits in the source-sync part of the system: the system reaches out to outside services, reads what changed, and turns it into standard records that can later be stored, searched, or recalled. It does not send messages or edit calendars.

Each file is a bridge to one service. Gmail reads mailbox changes and converts emails into plain searchable text. Outlook uses Microsoft Graph, Microsoft’s web API, to read mail, threads, contacts, calendar events, and folders as steady change streams. Google Calendar reads calendar events and separately records attendees, so the system knows both what happened and who was invited. Calendly reads scheduling data such as event types, groups, scheduled events, and invitees. Google Meet pulls transcripts and generated meeting notes as readable pages. Microsoft Teams reads teams, channels, chats, and messages. Slack reads users, channels, messages, threads, and senders. Together, these connectors act like intake clerks, translating many outside formats into one syncable shape.

## Files in this stage

### Scheduling connectors
Calendly provides read-only scheduling data such as users, event types, scheduled events, groups, and invitees.

### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `during Calendly source sync`

Calendly stores scheduling data behind a web API, and this connector is the bridge from that API into this project. Without it, the system would not know which Calendly endpoints to call, how to page through long result lists, or how to attach useful context like the organization or scheduled event an invitee belongs to.

The file defines the Calendly streams the system can sync. A stream is one kind of data, such as scheduled events or organization memberships. Most Calendly data is tied to an organization, so the connector first asks Calendly who the current user is and reads that user's current organization. It then uses that organization when requesting other collections.

Calendly returns large collections in pages, like a book split across many sheets. This connector follows Calendly's next page token until all pages have been read. For some streams it also supports incremental syncing, meaning it can ask only for records after a saved point in time instead of rereading everything.

Invitees need extra work. Calendly lists invitees under each scheduled event, so the connector first reads events, extracts each event's ID from its URI, then asks for invitees for that event. Finally, the flattening step adds simple top-level fields such as name, email, title, start time, and location so downstream search or display code can use the records more easily.

#### Function details

##### `_uuid_from_uri`  (lines 59–62)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID out of a Calendly URI. It is used when the connector has a full event URI but needs just the event ID to call the invitees endpoint.

**Data flow**: It receives any value. If the value is a non-empty string, it removes a trailing slash if present and returns the text after the last slash. If the input is missing or not a string, it returns nothing.

**Call relations**: When invitees are being synced, CalendlyConnector._invitees uses this helper to turn each scheduled event's URI into the event UUID needed for the next API request.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 70–73)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the authenticated account's user profile. The connector needs this because the profile tells it which organization to read data from.

**Data flow**: It takes an HTTP client that already knows how to make requests. It calls Calendly's /users/me endpoint, looks for the resource object in the response, and returns that object if it is a dictionary. If the response is not shaped as expected, it returns an empty dictionary.

**Call relations**: CalendlyConnector.paginate calls this directly for the api_user stream. CalendlyConnector._org_stream also calls it before reading organization-based streams, because those streams need the current organization value.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 75–88)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Calendly collection page by page. It hides the details of Calendly's pagination so the rest of the connector can simply loop over batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector machinery to read records from the response's collection field, follow pagination.next_page_token for the next page, request up to 100 records at a time, and yield each page as a list of dictionaries.

**Call relations**: CalendlyConnector._org_stream uses it for normal organization-level collections. CalendlyConnector._invitees uses it again for invitee lists under each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 90–106)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly collection that belongs to the current organization. It also adds the organization value to each returned record so downstream code knows where the record came from.

**Data flow**: It receives an HTTP client, an API path, and optionally a saved cursor plus the Calendly query parameter that should receive that cursor. It first reads the current user, extracts current_organization, and stops the stream with StreamSkipped if Calendly does not provide one. Then it builds request parameters with the organization and optional cursor, reads all pages, adds organization context to each page, and yields those enriched pages.

**Call relations**: CalendlyConnector.paginate uses this as the main path for event types, groups, organization memberships, and scheduled events. CalendlyConnector._invitees also uses it to find the scheduled events before fetching invitees. It relies on CalendlyConnector._current_user for the organization and CalendlyConnector._paginate_collection for the actual page reading.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 108–126)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled events. Calendly does not expose invitees as one simple organization-wide list here, so the connector must visit each event and then ask for that event's invitees.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads scheduled events through CalendlyConnector._org_stream. For each event, it extracts the event UUID from the event URI. It then reads invitee pages from that event's invitees endpoint. If a cursor is present, it keeps only invitees whose created_at value is newer than the cursor. For non-empty batches, it adds the parent scheduled event URI and UUID as context and yields the invitees.

**Call relations**: CalendlyConnector.paginate calls this when syncing the event_invitees stream. This function calls CalendlyConnector._org_stream to find events, _uuid_from_uri to get the ID needed for the invitee URL, CalendlyConnector._paginate_collection to read invitee pages, and with_context to preserve the connection between invitees and their event.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 128–160)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing function for reading Calendly streams. Given a stream name, it chooses the right Calendly endpoint and yields records in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For api_user, it returns the current user as a single-record page. For organization-based streams, it delegates to CalendlyConnector._org_stream, sometimes passing the cursor as Calendly's updated_since or min_start_time filter. For event_invitees, it delegates to CalendlyConnector._invitees. If the stream name is unknown, it raises StreamSkipped so the sync does not pretend unsupported data was read.

**Call relations**: The broader source-sync framework calls this to obtain records for each declared Calendly stream. It hands the work to CalendlyConnector._current_user, CalendlyConnector._org_stream, or CalendlyConnector._invitees depending on the stream being synced.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 162–202)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Calendly records into a friendlier form for storage, search, or display. It keeps the original record but adds common top-level fields such as name, email, title, and times where they are useful.

**Data flow**: It receives one Calendly record and the stream it came from. Based on the stream name, it copies the record and adds selected fields at the top level. For organization memberships, it safely reads the nested user object. For scheduled events, it turns Calendly's name into title, start_time into start_at, end_time into end_at, and extracts a readable location when possible. If the stream has no special rules, it returns the record unchanged.

**Call relations**: After CalendlyConnector.paginate yields raw records, the source framework can call this function before writing or indexing them. It uses dict_or_empty for organization membership records so missing or malformed nested user data does not break flattening.

*Call graph*: 1 external calls (dict_or_empty).


### Google communications and meetings
Google connectors ingest mailbox changes, calendar events and attendees, and Meet transcripts or notes as searchable source records.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync`

Gmail does not store an email as one simple text field. A message is a nested MIME tree, meaning a bundle of parts such as plain text, HTML, headers, and attachments. The readable email body is often hidden inside encoded pieces. This connector is the adapter that knows Gmail’s shape and translates it into records the rest of the system can remember and search.

On a first run, it lists all Gmail message IDs, fetches each message body, flattens the useful parts, and saves Gmail’s latest history marker as a cursor. A cursor is like a bookmark: next time, the connector asks Gmail what changed since that bookmark. It collects newly added messages, notes deleted ones as tombstones, and fetches only the new bodies. If Gmail says the bookmark is too old, the connector raises a special “cursor expired” signal so the larger sync system can start over safely. If the account lacks permission to read Gmail, it marks the stream as skipped rather than treating it as a broken run.

The file also controls how an email is shown to users. Instead of dumping Gmail’s raw JSON, it renders a readable block with From, To, Cc, Subject, and a clean body. If only HTML is available, it strips tags and keeps readable text.

#### Function details

##### `GmailConnector.paginate`  (lines 69–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Gmail messages. It decides whether to do a full mailbox read or an incremental read from a saved Gmail history bookmark, then yields pages of records and deletes to the core sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. With no cursor, it asks for every message ID; with a cursor, it asks Gmail for changes since that cursor. It fetches message bodies in chunks, then outputs StreamPage objects containing new records, deleted message IDs, and the next cursor. If Gmail refuses access because the grant lacks the needed read permission, it turns that into a skip signal.

**Call relations**: The larger connector framework calls this when it wants Gmail data. It delegates the first-run path to GmailConnector._backfill, the incremental path to GmailConnector._history, and body loading to GmailConnector._fetch_bodies. It then hands StreamPage results back to the sync engine.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 103–120)

```
async def _backfill(self, client: httpx.AsyncClient) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time mailbox scan. It gathers every Gmail message ID so the system can fetch and store the mailbox from scratch.

**Data flow**: It receives an HTTP client and repeatedly calls Gmail’s message list endpoint, following page tokens until there are no more pages. It collects valid message IDs into a list, then asks GmailConnector._seed_history_id for a starting history bookmark. It returns the full list of IDs and that bookmark.

**Call relations**: GmailConnector.paginate uses this when there is no saved cursor. After collecting IDs, it hands off to GmailConnector._seed_history_id so future runs can switch from full reads to change-only reads.

*Call graph*: calls 1 internal fn (_seed_history_id); called by 1 (paginate).


##### `GmailConnector._seed_history_id`  (lines 122–134)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str]) -> str | None
```

**Purpose**: This finds the Gmail history marker to save after a first full scan. That marker lets the next sync ask only for changes after the initial snapshot.

**Data flow**: It receives an HTTP client and the list of message IDs found during backfill. If the list is empty, it returns no cursor. Otherwise it fetches a minimal version of the newest listed message and reads its historyId. If that message disappeared meanwhile, it safely returns no cursor; otherwise it returns the history ID string when present.

**Call relations**: GmailConnector._backfill calls this at the end of a first-time scan. Its result goes back through GmailConnector.paginate as the next cursor for the sync engine to remember.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 136–171)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log since a saved history ID. It tells the sync system which messages were added, which were deleted, and what history ID should be saved next.

**Data flow**: It receives an HTTP client and an old history ID. It walks Gmail’s history pages, pulling message IDs from added and deleted entries. It removes messages that appear in both sets, returns the net new IDs, returns deleted IDs, and returns the latest history ID. If Gmail says the old history ID has expired, it raises CursorExpired so a full refetch can happen.

**Call relations**: GmailConnector.paginate calls this on incremental runs. It relies on _message_ids to pull IDs out of Gmail’s nested history entries, and it signals CursorExpired when the surrounding sync should abandon the stale cursor.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 173–187)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This fetches the full content for a list of Gmail messages. It turns lightweight message IDs into flattened records the rest of the source system can store.

**Data flow**: It receives an HTTP client and a list of message IDs. For each ID, it asks Gmail for the full message. If a message vanished between listing and fetching, it skips that one. For each successfully fetched message, it passes the raw Gmail response to _flatten_message and returns the list of flattened records.

**Call relations**: GmailConnector.paginate calls this after deciding which message IDs are new. It hands each raw Gmail message to _flatten_message so the rest of the system does not have to understand Gmail’s nested payload format.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 189–208)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Gmail record into readable prose for recall or search. It avoids showing users raw Gmail JSON and instead formats the email like a person would read it.

**Data flow**: It receives one flattened record and the stream it belongs to. For the messages stream, it pulls the subject, sender, recipients, and body, formats them into a header block and message text, and returns a title plus rendered content. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The source framework calls this when it needs human-readable text for a Gmail record. It uses _str for safe subject text, _format_contact for the sender, _format_recipients for recipient lists, and _message_body to choose the best available body text.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 211–220)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts Gmail message IDs from history entries. Gmail wraps IDs inside nested objects, so this helper pulls out just the usable strings.

**Data flow**: It receives an unknown value that should be a list of Gmail history entries. It ignores malformed entries, looks inside each entry’s message object, and collects non-empty string IDs. It returns a clean list of message ID strings.

**Call relations**: GmailConnector._history calls this while walking Gmail’s change log. The cleaned IDs feed into the added and deleted sets used to build the incremental sync result.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 223–250)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts Gmail’s raw message response into the project’s simpler message record. It lifts out the useful headers, addresses, labels, and decoded body text.

**Data flow**: It receives a raw Gmail message dictionary. It reads selected headers such as From, To, Cc, and Subject, extracts plain-text and HTML bodies, parses addresses, gathers labels, and decides whether the message is inbound or outbound. It returns one flat dictionary with predictable fields.

**Call relations**: GmailConnector._fetch_bodies calls this after each successful Gmail body fetch. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 253–267)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail message payload for the first plain-text body and the first HTML body. Gmail stores message parts in a tree, so this helper walks that tree.

**Data flow**: It receives the message payload dictionary. It recursively visits the payload and its child parts, looking for text/plain and text/html parts that contain encoded body data. It decodes the first body found for each type and returns a pair: plain text, then HTML, either of which may be missing.

**Call relations**: _flatten_message calls this while building the flat record. Its inner walk function does the actual tree traversal and calls _b64url_decode when it finds encoded Gmail body text.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 257–264)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive worker inside _extract_bodies. It visits one MIME part, checks whether it contains useful body text, then visits any child parts.

**Data flow**: It receives one part of Gmail’s payload tree. If the part is plain text or HTML and has encoded data, it decodes and saves it unless that type was already found. It then repeats the same process for each child part. It changes the surrounding found dictionary rather than returning a separate value.

**Call relations**: _extract_bodies starts this worker on the root payload. Whenever a body part is found, the worker hands the encoded text to _b64url_decode so it becomes readable Unicode text.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 270–276)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body-part text. Gmail uses URL-safe base64, a compact text encoding, and sometimes omits padding characters, so this helper fixes that before decoding.

**Data flow**: It receives an encoded string from Gmail. It adds any missing padding, decodes it with URL-safe base64, and converts the bytes into text using UTF-8 while replacing broken characters. If decoding fails, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this when it finds a plain-text or HTML body inside Gmail’s MIME tree. The decoded result becomes the body text stored in the flattened record.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 279–286)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses the first email address from a header such as From. It separates the email handle from the display name.

**Data flow**: It receives a header string or nothing. If there is no header or no parseable address, it returns two missing values. Otherwise it uses Python’s email address parser, lowercases the email address, keeps the display name if present, and returns both.

**Call relations**: _flatten_message calls this for the From header. The resulting handle and display name become the sender fields used later by GmailConnector.render.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 289–296)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses a recipient header such as To or Cc into a list of structured contacts. It turns one messy header string into small dictionaries with email addresses and optional names.

**Data flow**: It receives a header string or nothing. With no header, it returns an empty list. Otherwise it parses all addresses, drops entries without an address, lowercases each email address, and returns a list of contact dictionaries.

**Call relations**: _flatten_message calls this for To and Cc headers. The resulting lists are later formatted for display by _format_recipients during rendering.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 299–304)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This formats one contact for display. It shows either an email address alone or a display name followed by the address in angle brackets.

**Data flow**: It receives a possible email handle and possible display name. If the handle is missing or not a string, it returns an empty string. If a display name is available, it returns 'Name <email>'; otherwise it returns just the email.

**Call relations**: GmailConnector.render uses this to format the sender. _format_recipients also uses it for each recipient in a list, so all contacts are displayed consistently.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 307–314)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient contact dictionaries into one readable line. It is used for To and Cc fields in the rendered email.

**Data flow**: It receives a value that should be a list of contact dictionaries. If it is not a list, it returns an empty string. Otherwise it formats each dictionary with _format_contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this while building the email header block. It relies on _format_contact so recipient formatting matches sender formatting.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 317–325)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body text for a Gmail record. It prefers real plain text, falls back to cleaned HTML, and finally uses Gmail’s snippet if no body is available.

**Data flow**: It receives a flattened message record. If body_text is a non-empty string, it returns the trimmed text. Otherwise, if body_html is present, it converts the HTML to readable text. If neither body exists, it returns the trimmed snippet when available or an empty string.

**Call relations**: GmailConnector.render calls this when assembling the final readable email. It is the final decision point for what body content a user will see or search.

*Call graph*: called by 1 (render).


##### `_str`  (lines 328–329)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is already a string. It prevents non-text values from being accidentally used as email text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this for the subject before using it in the rendered title and header.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 336–338)

```
def __init__(self) -> None
```

**Purpose**: This prepares an HTML-to-text parser. It creates the internal list where readable pieces of text and line breaks will be collected.

**Data flow**: It receives no outside data beyond the new parser object being created. It initializes the base HTML parser with automatic character-reference conversion, then starts an empty parts list. The result is a parser ready to receive HTML.

**Call relations**: _HtmlText.extract creates this parser when HTML email content needs to be turned into plain text. The parser’s later callback methods fill the parts list as the HTML is read.


##### `_HtmlText.extract`  (lines 341–346)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into readable plain text. It keeps human-visible text, adds line breaks around block-like tags, and removes tags and attributes.

**Data flow**: It receives raw HTML as a string. It feeds that HTML into a new _HtmlText parser, joins the collected pieces, normalizes extra spaces on each line, drops empty lines, and returns a clean text string.

**Call relations**: This is the public helper for the _HtmlText class. When the message body has HTML but no plain text, the rendering path uses this kind of conversion so the email is still readable.


##### `_HtmlText.handle_data`  (lines 348–349)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This parser callback records visible text found inside HTML. It is how actual words from the email body are kept.

**Data flow**: It receives a text fragment from the HTML parser. It appends that fragment to the parser’s parts list. It does not return a value; it changes the parser’s collected text.

**Call relations**: Python’s HTML parser calls this while _HtmlText.extract feeds it HTML. The collected data is later joined and cleaned by _HtmlText.extract.


##### `_HtmlText.handle_starttag`  (lines 351–353)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This parser callback adds a line break when an opening HTML tag represents a block boundary. It helps paragraphs, list items, and table cells not run together.

**Data flow**: It receives an HTML tag name and its attributes. If the tag is one of the known block-like tags, it appends a newline to the parser’s parts list. Attributes are ignored because the goal is readable text, not preserving HTML.

**Call relations**: Python’s HTML parser calls this during _HtmlText.extract. Its inserted line breaks are part of why cleaned HTML bodies remain readable instead of becoming one long sentence.


##### `_HtmlText.handle_endtag`  (lines 355–357)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This parser callback adds a line break when a closing HTML tag ends a block of content. It preserves natural separation between sections of an HTML email.

**Data flow**: It receives an HTML tag name. If the tag is one of the known block-like tags, it appends a newline to the parser’s parts list. It returns nothing and only changes the parser’s collected output.

**Call relations**: Python’s HTML parser calls this while _HtmlText.extract processes HTML. Together with _HtmlText.handle_starttag and _HtmlText.handle_data, it shapes the final plain-text version of an HTML-only email.


### `extensions/sources/ufo_ext_sources/google_calendar.py`

`io_transport` · `during Google Calendar source sync`

This connector is the bridge between Google Calendar and the project’s source-sync system. Without it, calendar events would stay inside Google and could not be indexed, recalled, or related to people through attendees.

It reads from Google’s Calendar API, specifically the primary calendar’s events endpoint. On the first run, it looks back 90 days and asks Google for events, including deleted ones. Google then gives back a sync token, which works like a bookmark saying “next time, start from here.” Later runs use that token to fetch only changes instead of reading the whole calendar again.

The file produces two views of the same calendar data. The main `calendar_events` stream keeps one record per event, including title, time, location, description, organizer, and attendee summaries. The `event_attendees` stream turns each event’s attendee list into separate rows, one per person invited. This is like keeping both a meeting agenda and a sign-in sheet.

It also deals with common API problems. If Google says the bookmark expired, it raises a special “cursor expired” signal so the wider system can start fresh. If the user’s authorization does not allow calendar access, it marks the stream as skipped rather than treating the whole sync as broken.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches Google Calendar events page by page and turns them into sync pages for the rest of the system. It supports both the event stream and the attendee stream, using Google’s sync token as a bookmark for incremental updates.

**Data flow**: It receives an HTTP client, a stream choice, and an optional stored cursor. If there is a cursor, it asks Google for changes since that cursor; otherwise it starts with a 90-day lookback. For each Google page, it separates useful records from cancelled events, converts active events or attendees into the project’s flat record shape, and yields `StreamPage` objects. At the end, it passes along Google’s next sync token as the new cursor. If Google rejects the token or permissions, it raises a clear signal for the sync runner.

**Call relations**: The source-sync runner calls this when it needs calendar data. Inside the loop, it delegates event shaping to `_flatten_event` for `calendar_events` and attendee-row creation to `_flatten_attendees` for `event_attendees`. It hands finished batches back as `StreamPage` objects, or tells the wider system to refresh or skip when Google returns specific error statuses.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Builds a readable text version of a calendar event so it can be displayed or indexed as meaningful content. For non-event streams, it falls back to the base connector’s default rendering behavior.

**Data flow**: It receives one already-flattened record and the stream it belongs to. For calendar events, it reads the title, time range, location, attendee handles, and description, then formats them into a short document-like body. It returns two strings: a title and the rendered text body.

**Call relations**: The wider indexing or recall flow calls this after records have been synced. It uses `_str` to safely turn a possibly missing or non-text title into a string, then produces human-readable event text instead of exposing raw API fields.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–163)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns one raw Google Calendar event into the project’s standard event record. This strips away Google-specific nesting and keeps the fields the sync system cares about.

**Data flow**: It receives one raw event dictionary from Google. It reads the event id, title, description, location, start and end times, organizer, recurrence identifiers, status, and attendee list. It normalizes times through `_parse_when`, turns attendees into compact attendee summaries through `_attendee`, and returns one flat dictionary ready to be stored.

**Call relations**: GoogleCalendarConnector.paginate calls this for active events in the `calendar_events` stream. It acts as the translator between Google’s event format and the project’s record format, relying on `_attendee` and `_parse_when` for the smaller translation jobs.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 166–172)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Creates a small attendee summary for embedding inside an event record. It keeps the person’s email handle, display name, and response in a consistent internal wording.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee email, copies the display name, maps Google’s response value such as `needsAction` into the project’s style such as `needs_action`, and returns a compact dictionary.

**Call relations**: _flatten_event calls this while building the attendee list that lives inside a calendar event record. It does not create separate attendee rows; that larger job is done by `_flatten_attendees`.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 175–199)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one raw Google Calendar event into separate attendee records. This lets the system store and query invitees as their own rows, not only as text inside an event.

**Data flow**: It receives one raw event dictionary from Google. It finds the event id, organizer email, and each valid attendee email. For every attendee, it builds a row with a unique id made from the event id and attendee handle, plus role, response, display name, and whether the attendee is the calendar owner. It returns a list of attendee rows.

**Call relations**: GoogleCalendarConnector.paginate calls this when syncing the `event_attendees` stream. For each attendee, it asks `_attendee_role` to decide whether the person is the organizer, a resource like a room, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 202–209)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what kind of participant an attendee is in an event. This gives each attendee row a simple role such as organizer, resource, optional, or required.

**Data flow**: It receives one attendee dictionary and a flag saying whether that attendee is the organizer based on email. It checks organizer markers first, then resource and optional flags, and returns the matching role string. If none of those special cases apply, it returns `required`.

**Call relations**: _flatten_attendees calls this while creating each attendee row. It supplies the role field that makes the attendee stream more useful than a plain list of email addresses.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 212–221)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar start and end times into one text timestamp format. Google uses one shape for timed events and another for all-day events, so this function smooths over that difference.

**Data flow**: It receives a value that may be a Google time dictionary. If it contains `dateTime`, it returns that timestamp as text. If it contains an all-day `date`, it turns the date into a midnight UTC timestamp. If the input is missing or not in the expected shape, it returns nothing.

**Call relations**: _flatten_event calls this for an event’s start and end fields. This keeps the main event-flattening code from having to know the details of Google’s two different time formats.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 224–225)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents rendering code from accidentally treating missing or non-text values as titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: GoogleCalendarConnector.render calls this when choosing the event title. It is a small safety helper that keeps rendered output clean even when Google data is incomplete or oddly shaped.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/google_meet.py`

`io_transport` · `sync run`

Google Meet stores meeting artifacts in several separate places: a conference record, transcript sessions, individual transcript lines, and sometimes a Google Docs file containing AI notes. This connector gathers those scattered pieces and presents them as one understandable page per meeting.

The main flow starts by listing recent conference records from the Google Meet API. If the system has synced before, it asks for meetings starting slightly before the last saved time. That one-day “lookback” matters because transcripts and AI notes may appear after a meeting ends. For each conference, the connector fetches any transcript sessions and smart-note sessions. Conferences with no artifacts are not emitted as pages, but their start times still help advance the sync cursor so the system does not keep rechecking the same old window forever.

For transcripts, it fetches speaker entries and keeps the Google Docs destination link when Google provides one. For smart notes, it also tries to read the linked Google Doc and inline its plain text. If Google refuses access to a document or the document is gone, the connector keeps going and leaves the link rather than failing the whole sync. If the whole Meet API is refused because the account lacks permission, the stream is marked skipped rather than broken.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 52–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main fetch loop for the Google Meet meeting artifacts stream. It asks Google Meet for conference records, turns each useful conference into a record, and yields pages of records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor time. It builds Google Meet API request parameters, optionally applies a one-day lookback filter, fetches conference pages, asks `_conference_record` to enrich each conference, keeps only meetings that have transcripts or smart notes, and returns `StreamPage` objects with records plus the next cursor. If Google returns an authorization refusal, it changes that into a skipped stream message instead of a hard failure.

**Call relations**: The sync driver calls this when it needs Google Meet data. During each page, it uses `_lookback` to avoid missing late artifacts, `_max_start_time` to advance the saved position, and `_conference_record` to collect the details for each meeting before handing the finished page back to the driver.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 88–111)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete internal record for one Google Meet conference. It gathers the conference's transcripts and smart notes and combines them with the meeting's basic timing and identity details.

**Data flow**: It takes one raw conference object from Google and reads its resource name, space, start time, end time, and expiry time. It fetches transcript artifacts and smart-note artifacts under that conference, converts each one into a simpler record, then returns one dictionary representing the meeting.

**Call relations**: `paginate` calls this for every conference returned by Google. It delegates artifact listing to `_artifacts`, transcript shaping to `_transcript`, and smart-note shaping to `_smart_note`, then gives the completed meeting record back to `paginate`.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 113–130)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind under a conference, such as all transcript sessions or all smart-note sessions. It hides Google API pagination so callers receive a simple list.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the collection name to fetch. If there is no parent name, it returns an empty list. Otherwise it repeatedly calls the relevant Google Meet API endpoint, follows page tokens, collects artifact items, and returns the full list.

**Call relations**: `_conference_record` calls this twice for each conference: once for transcripts and once for smart notes. It hands those raw artifact lists back so `_conference_record` can pass them to the more specific conversion functions.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 132–144)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet transcript session into the connector's simpler transcript shape. It includes the transcript's Google Docs destination and its per-speaker entries.

**Data flow**: It receives a raw transcript object. It extracts the transcript name, state, start and end times, document link information, then calls `_transcript_entries` to fetch the spoken lines. It returns a dictionary containing the transcript metadata and entries.

**Call relations**: `_conference_record` calls this for each transcript artifact found by `_artifacts`. It uses `_docs_destination` for Google Docs link fields and `_transcript_entries` for the detailed dialogue before returning the transcript to the meeting record.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 146–179)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual spoken lines inside a transcript. These entries are what make the final rendered page read like a meeting dialogue instead of only a metadata record.

**Data flow**: It receives a transcript resource name. If the name is missing, it returns an empty list. Otherwise it calls the Google Meet entries endpoint page by page, extracts each entry's id, participant, text, language, and timing, and returns the collected list. If Google says the entries are inaccessible or missing, it returns whatever it has already collected instead of failing.

**Call relations**: `_transcript` calls this while building a transcript record. Later, the render path passes these entries through `_dialogue` so the final page can show speaker-labeled text.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 181–196)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note session into a simpler record and, when possible, includes the text of the linked Google Doc. Smart notes are Google's AI-generated meeting summaries.

**Data flow**: It receives a raw smart-note object. It extracts id, name, state, timing, and Google Docs destination details. If there is a document id, it calls `_document_text` to try to read the document's plain text and adds that text as `body` when available. It returns the smart-note dictionary either way.

**Call relations**: `_conference_record` calls this for every smart-note artifact found under a meeting. It hands document reading to `_document_text` and then returns the enriched note so the meeting page can include an AI summary section.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 198–207)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads the plain text from a Google Docs document linked from a smart note. It makes AI notes searchable in the system instead of only storing a link.

**Data flow**: It receives a Google Docs document id. It safely inserts that id into the Docs API URL, fetches the document, and passes the returned structure to `_plain_text`. If Google says the document is forbidden or missing, it returns an empty string. Otherwise it returns the extracted text.

**Call relations**: `_smart_note` calls this only when a smart note points to a Google Doc. It relies on `_plain_text` to turn the nested Docs API response into readable text, then gives that text back to `_smart_note` for inclusion in the record.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 209–226)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one collected meeting record into a human-readable text page. It is the final formatting step that makes the synced data useful to readers and search tools.

**Data flow**: It receives a record and a stream description. For the meeting artifacts stream, it reads the title, conference details, transcript list, and smart-note list, formats them into Markdown-style text, and returns the page title plus body. For other streams, it falls back to the base connector's rendering behavior.

**Call relations**: After `paginate` has produced records, the wider source framework calls `render` to create readable output. It calls `_labeled` for simple metadata blocks, `_transcripts_section` for transcript text, and `_smart_notes_section` for AI summaries.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 229–245)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats the transcript part of a meeting page. It gives each transcript a small metadata block and then adds the speaker dialogue.

**Data flow**: It receives a value that should contain transcript records. It treats missing or non-list values as empty, then for each transcript reads state, timing, document URL, and entries. It uses `_dialogue` to turn entries into speaker lines and returns one formatted text section.

**Call relations**: `GoogleMeetConnector.render` calls this while building the full meeting page. It calls `_labeled` for transcript metadata and `_dialogue` for the spoken content, then hands the finished section back to `render`.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 248–264)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats the AI summaries part of a meeting page. It shows smart-note metadata and includes the note body when the linked Google Doc could be read.

**Data flow**: It receives a value that should contain smart-note records. It normalizes that value to a list, then reads each note's state, timing, document URL, and body text. It returns a formatted section headed as AI summaries, or an empty string if there are no notes.

**Call relations**: `GoogleMeetConnector.render` calls this after formatting the conference metadata and transcripts. It uses `_labeled` for the small metadata block and gives the completed smart-note text back to the render method.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 267–280)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into readable speaker lines. It also joins back-to-back entries from the same speaker so the transcript feels less choppy.

**Data flow**: It receives a value that should contain transcript entries. For each entry with text, it finds a speaker label, then either appends the text to the previous line if the speaker is unchanged or starts a new `Speaker: text` line. It returns the joined dialogue as plain text.

**Call relations**: `_transcripts_section` calls this when it needs the spoken content for a transcript. It uses `_speaker` to choose a readable speaker name and `_str` to safely ignore non-text values.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 283–296)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. It strips away document layout details and keeps the actual paragraph text.

**Data flow**: It receives a Google Docs document record. It looks inside the document body, walks through content items, paragraphs, elements, and text runs, collects string content, joins it together, trims extra space, and returns the result.

**Call relations**: `_document_text` calls this after successfully fetching a Google Doc. It does not make network calls itself; it is the text-cleaning step between the Docs API response and the smart-note record.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 299–306)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls Google Docs destination information out of a Meet transcript or smart-note record. It preserves both the document id and the export URL when Google provides them.

**Data flow**: It receives a raw artifact record. If the `docsDestination` field is not a dictionary, it returns an empty dictionary. Otherwise it reads the document id and export link, converts only real strings through `_str`, and returns them under consistent field names.

**Call relations**: `_transcript` and `_smart_note` both call this because both artifact types can point to Google Docs. Its output becomes part of the stored record and later appears in rendered transcript or AI summary sections.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 309–315)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This finds the newest conference start time in a page of Google Meet results. That time becomes the sync cursor, which is the bookmark for the next run.

**Data flow**: It receives a list of conference records and the current cursor. It compares each conference's start time string with the current value and keeps the largest one. It returns the updated cursor, or the original cursor if no newer start time is found.

**Call relations**: `paginate` calls this after fetching each page of conferences. The returned value is placed into the `StreamPage` so the sync system knows how far it has safely progressed.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 318–320)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor time backward by one day. It helps the connector catch transcripts or notes that Google generated after the original meeting sync.

**Data flow**: It receives an ISO-style timestamp string, parses it as a date and time, subtracts the configured one-day lookback, and returns a timestamp string formatted for Google's filter syntax.

**Call relations**: `paginate` calls this when a previous cursor exists. The adjusted time is used in the Google Meet API filter so the next run refetches a small recent window instead of only strictly new meetings.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 323–324)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the short id from a Google resource name. Google resource names often look like paths, and this helper keeps only the final piece.

**Data flow**: It receives a resource name string. If the string is present, it splits it at the last slash and returns the part after that slash. If it is empty, it returns an empty string.

**Call relations**: Several record-building functions call this when they need stable, compact ids for conferences, transcripts, transcript entries, smart notes, or speakers. `_speaker` also uses it to turn a participant resource name into a readable label.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 327–329)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses the label used before each transcript line. It turns Google's participant value into a short speaker name and falls back to `Participant` when no usable name exists.

**Data flow**: It receives a participant value of any type. It keeps it only if it is a string, extracts the final resource id, and returns that id or the default label `Participant`.

**Call relations**: `_dialogue` calls this for every transcript entry that has text. The returned speaker label becomes the left side of each rendered dialogue line.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 332–333)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that accepts only real strings. It prevents accidental non-string values from leaking into formatted text fields.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Many functions use this while reading API responses, because external APIs can omit fields or return unexpected shapes. It keeps `_conference_record`, `_transcript`, `_smart_note`, rendering helpers, and document-link extraction simple and safe.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 336–337)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats a small block of `label: value` lines and skips empty values. It is used for metadata like start time, end time, state, and document links.

**Data flow**: It receives a list of label-and-value pairs. It keeps only pairs where the value is non-empty, formats each as `label: value`, joins them with newlines, and returns the resulting text block.

**Call relations**: `GoogleMeetConnector.render`, `_transcripts_section`, and `_smart_notes_section` call this whenever they need a neat metadata block. It gives those higher-level renderers a consistent way to show details without blank lines for missing data.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### Microsoft collaboration connectors
Microsoft Graph connectors read Teams collaboration data and Outlook mail, contacts, folders, threads, and calendar events into syncable streams.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `during Microsoft Teams source sync`

This connector is the bridge between the project and Microsoft Teams. Without it, the system would not know how to ask Microsoft for a user’s joined teams, channels, chats, or messages, nor how to turn those replies into the project’s standard stream of records.

Microsoft Teams data is fetched through Microsoft Graph, which returns lists in pages. Think of it like reading a long email thread one screen at a time: each response gives some items and may point to the next page. This file follows those pages for joined teams, team channels, channel messages, chats, and chat messages.

The connector is read-only. It does not create or edit anything in Teams. It also does not keep a Microsoft access token itself; authentication is supplied by the wider source-running system.

Messages are synced incrementally. That means the connector can receive a saved “cursor,” or watermark, and only pass on messages whose last modified time is newer than that. This avoids reprocessing old messages unnecessarily.

Some Teams areas may be inaccessible even when others are allowed. If one team, channel, or chat refuses access or disappears, the connector skips that parent and keeps going. But if Microsoft refuses the main listing because the permission grant is missing, the stream is marked as skipped rather than treated as a crash.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 54–58)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Microsoft Teams that the signed-in user has joined. It gathers all pages into one list so later steps can use those teams as starting points.

**Data flow**: It receives an HTTP client that can talk to Microsoft Graph. It asks for `/me/joinedTeams`, follows each returned page, collects the team records into a list, and returns that complete list.

**Call relations**: This is the first step for team-based syncing. The channel fetcher uses it to know which teams to inspect, and the main pagination method uses it when the requested stream is the top-level teams stream.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 60–73)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches channels for each joined team. It adds the parent team’s identity to each channel record so the system can later understand where that channel came from.

**Data flow**: It starts by reading the user’s joined teams. For each valid team ID, it asks Microsoft Graph for that team’s channels, attaches context such as the team ID and team name, and yields channel pages one at a time. If a particular team cannot be read because access is denied or the team is gone, it skips that team and continues.

**Call relations**: This sits between team discovery and message discovery. The channel message fetcher relies on it to find channels, while the main pagination method uses it directly when syncing the channels stream.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 75–106)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from team channels, optionally only returning messages newer than the saved cursor. It preserves enough context to connect each message back to its team and channel.

**Data flow**: It receives an HTTP client and an optional cursor value. It walks through channels, asks Microsoft Graph for each channel’s messages, filters out older messages when a cursor is present, adds team, channel, and thread context, and yields only non-empty batches. If one channel cannot be read because access is denied or it no longer exists, it skips that channel and continues.

**Call relations**: This is used by the main pagination method when the system is syncing channel messages. It depends on the channel fetcher to supply the list of places where messages may live, then hands back message batches for the sync runner to store or index.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 108–112)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of chats visible to the signed-in user. These are separate from team channels and include personal or group chat threads.

**Data flow**: It receives an HTTP client, asks Microsoft Graph for `/me/chats`, follows all returned pages, collects the chat records into one list, and returns that list.

**Call relations**: This is the starting point for chat-based syncing. The chat message fetcher uses it to know which chat threads to inspect, and the main pagination method uses it for the top-level chats stream.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 114–134)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from each chat thread, optionally filtering to only messages newer than the saved cursor. It marks each message with the chat it belongs to.

**Data flow**: It receives an HTTP client and an optional cursor. It first gets the user’s chats, then asks Microsoft Graph for messages in each valid chat. If a cursor is present, it keeps only messages with a later last modified time. It adds chat and thread context before yielding message batches. If one chat cannot be read because access is denied or it is missing, it skips that chat and continues.

**Call relations**: This is called by the main pagination method when syncing chat messages. It uses the chat list as its map, visits each chat, and passes the resulting message batches back to the source sync pipeline.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 136–169)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses which Microsoft Teams stream to read and yields records for that stream. This is the connector’s main doorway used by the source syncing system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it calls the matching helper for teams, channels, channel messages, chats, or chat messages, then yields the pages those helpers produce. If Microsoft refuses a top-level request because the grant lacks permission, it turns that into a stream skip. If the stream name is unknown, it also reports that the stream is not implemented.

**Call relations**: The wider sync runner calls this when it needs records from a specific stream. This method acts like a dispatcher: it sends the request to the right Teams or chat helper, then gives the returned batches back to the runner in the connector’s standard format.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 171–177)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a Microsoft Teams record into readable text for storage or search. For messages, it extracts the subject and plain message body instead of leaving Microsoft’s HTML-shaped content untouched.

**Data flow**: It receives one record and the stream it came from. For non-message streams, it falls back to the normal rendering behavior from the base connector. For channel and chat messages, it reads the subject, pulls `body.content` from the nested record, strips HTML tags, builds a simple heading, and returns a title plus readable text.

**Call relations**: This is used after records have been fetched, when the system needs a human-readable page from each record. It relies on the small helper functions in this file to safely turn missing or non-text values into clean text.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 180–183)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a text value. Microsoft Graph stores Teams message bodies as HTML, so this helper makes the body easier to read as plain text.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces HTML tags with spaces, trims the result, and returns the cleaned text.

**Call relations**: The render method calls this when preparing channel and chat messages. It is a small cleanup step between Microsoft’s raw message body and the project’s readable page text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 186–187)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only when it is already text. It prevents non-text subjects from being treated as readable titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: The render method uses this when reading a message subject. It keeps title creation predictable even when Microsoft sends a missing or unexpected subject value.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `during Outlook source sync`

This connector is the system’s Outlook reader. Its job is to ask Microsoft Graph, Microsoft’s web API for Outlook and other Microsoft 365 data, what exists in a mailbox and what has changed since the last sync. Without it, Outlook accounts could not be indexed or kept up to date.

The main idea is a “delta feed,” which works like asking, “Show me everything the first time, and after that only show me what changed.” Microsoft returns pages of records, plus a special link that becomes the next cursor. A cursor is like a bookmark: the next run starts from it instead of rereading the whole mailbox.

Messages and contacts are a little more complex because they can live in folders. This file keeps one bookmark per folder, stored as JSON. Calendar events use a time window around today, so the connector asks for relevant past and future events. Conversations are not a separate Microsoft object here; the file builds them by reading messages and grouping them by conversation ID.

The connector also reshapes records into friendlier fields, such as contact email, message sender, event start time, and a plain-text event description. If Microsoft refuses access with a permission error, the stream is skipped rather than treated as a crash.

#### Function details

##### `_strip_html`  (lines 33–36)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Turns an HTML-looking text value into plain text by removing tags. This is used so calendar event descriptions are easier to search and display.

**Data flow**: It receives any value. If the value is not text, it returns nothing. If it is text, it replaces HTML tags with spaces, trims the result, and returns the cleaned string.

**Call relations**: OutlookConnector.flatten calls this when it is preparing event records. It helps convert Microsoft’s rich event body into a simpler description field.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 39–47)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact. Contacts can contain several addresses, so this chooses one simple email value for downstream use.

**Data flow**: It receives a contact record. It looks at the contact’s email address list, checks each entry for a nested address value, and returns the first non-empty address it finds. If there is no usable list or address, it returns nothing.

**Call relations**: OutlookConnector.flatten calls this while reshaping contact records. It uses get_path to safely read a nested field without crashing if part of the structure is missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 50–60)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from an Outlook contact. It prefers the mobile number, then falls back to business phone numbers.

**Data flow**: It receives a contact record. It first checks for a non-empty mobile phone string. If that is missing, it scans the business phone list and returns the first non-empty phone string. If none is found, it returns nothing.

**Call relations**: OutlookConnector.flatten calls this when building simpler contact fields. It turns Microsoft’s multi-field phone data into one convenient phone value.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate`  (lines 102–135)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Acts as the main dispatcher for reading each Outlook stream. Given a requested stream, it chooses the right Outlook-reading routine and yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream name, calls the matching paging method, and passes each page onward. If Microsoft returns a permission refusal, it turns that into a skipped stream message instead of a hard failure.

**Call relations**: The sync framework calls this when it needs Outlook data. It hands work to the stream-specific methods for conversations, messages, contacts, events, or mail folders, and raises StreamSkipped when the stream cannot be read or is unsupported.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages).


##### `OutlookConnector._conversation_pages`  (lines 137–166)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records from messages. Outlook messages have a conversation ID, so this method groups messages into email threads.

**Data flow**: It receives an HTTP client and an optional cursor. It asks Microsoft Graph for messages, optionally only those modified after the cursor. For each page, it keeps the newest message per conversation ID and emits simplified conversation records with title, snippet, last message time, and update time.

**Call relations**: OutlookConnector.paginate calls this for the conversations stream. Unlike the delta-based streams, this method derives conversation data from message pages rather than reading a separate conversation endpoint.

*Call graph*: called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 168–205)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads Microsoft Graph delta pages and converts them into the system’s standard page format. This is the shared engine for streams that support Microsoft’s change-feed bookmarks.

**Data flow**: It receives an HTTP client, an initial API path, an optional cursor link, and optional query parameters. It follows Microsoft’s next-page links, separates normal records from deleted items marked by Microsoft, and yields StreamPage objects containing records, deletion IDs, and the next cursor. It stops when Microsoft provides the final delta link instead of another page link.

**Call relations**: OutlookConnector.paginate uses this directly for mail folders, and the message, contact, and event methods use it as their common delta-reader. It creates StreamPage objects so the rest of the sync system can treat Outlook pages consistently.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 207–227)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads message changes from every mail folder. Because Outlook message deltas are tracked per folder, this method keeps a separate bookmark for each folder.

**Data flow**: It receives an HTTP client and an optional JSON cursor map. It decodes the map, lists mail folders, then reads each folder’s message delta feed. It adds the folder ID to each returned message, updates that folder’s cursor, re-encodes all folder cursors, and yields StreamPage objects with records, deletions, and the combined cursor.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It first asks _list_mail_folders which folders exist, uses _graph_delta_pages to read each folder, and uses _decode_cursor_map and _encode_cursor_map to preserve per-folder progress.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 229–256)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads contact changes from the default contacts area and any contact folders. Like messages, contacts need separate bookmarks per folder.

**Data flow**: It receives an HTTP client and an optional JSON cursor map. It decodes saved folder cursors, builds a list containing the default contact area plus named contact folders, reads each contact delta feed, updates that folder’s cursor, and yields StreamPage objects. If the default contacts delta endpoint is unavailable with certain not-found or bad-request errors, it skips that default area and continues.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It uses _list_contact_folders to discover folders, _graph_delta_pages to read Microsoft’s delta feed, and the cursor encode/decode helpers to remember progress per contact folder.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 258–269)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads calendar event changes in a practical time window around the current date. This keeps the sync focused on events likely to matter instead of asking for all calendar history.

**Data flow**: It receives an HTTP client and an optional cursor. It calculates a window from one year in the past to two years in the future, then asks the calendar-view delta endpoint for pages in that range. It yields each resulting page unchanged.

**Call relations**: OutlookConnector.paginate calls this for the events stream. It relies on _graph_delta_pages for the actual Microsoft delta paging, adding only the calendar time-window parameters.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 271–278)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Finds the IDs of the mailbox’s mail folders. These IDs are needed because message changes are read folder by folder.

**Data flow**: It receives an HTTP client. It asks Microsoft Graph for mail folders page by page, collects every non-empty folder ID, and returns the list of IDs.

**Call relations**: OutlookConnector._message_delta_pages calls this before reading message deltas. The returned folder IDs become the set of folders whose message feeds are checked.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 280–287)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Finds the IDs of custom contact folders. These IDs let the connector read contacts outside the default contacts area.

**Data flow**: It receives an HTTP client. It asks Microsoft Graph for contact folders page by page, collects every non-empty folder ID, and returns the list.

**Call relations**: OutlookConnector._contact_delta_pages calls this before reading contact deltas. The returned IDs are combined with the special default contact area so all contact locations can be checked.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 289–319)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adds simpler, standardized fields to Outlook records. This makes raw Microsoft Graph records easier for the rest of the system to search, display, and compare.

**Data flow**: It receives one record and the stream it belongs to. For contacts, it adds names, a chosen email, phone, and created time. For messages, it adds subject, snippet, sender address, sent time, and thread IDs. For events, it adds title, cleaned description, start and end times, and location. For other streams, it returns the record as-is.

**Call relations**: The source framework calls this after records are fetched. It uses _first_email, _phone, _strip_html, and get_path to safely pull useful values out of Microsoft’s nested record shapes.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 322–331)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Turns a saved JSON cursor map back into a folder-to-bookmark dictionary. This lets message and contact syncs resume each folder from the right place.

**Data flow**: It receives a raw cursor string or nothing. If there is no string, invalid JSON, or the JSON is not an object, it returns an empty dictionary. Otherwise, it keeps only non-empty string values and returns a clean dictionary of folder IDs to cursor links.

**Call relations**: OutlookConnector._message_delta_pages and OutlookConnector._contact_delta_pages call this at the start of folder-based syncing. It protects the sync from bad or old cursor data by falling back to a fresh start.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 334–335)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns a folder-to-bookmark dictionary into a JSON string for saving. This is how the connector remembers progress across many folders between sync runs.

**Data flow**: It receives a dictionary of folder IDs to cursor links. If the dictionary has entries, it serializes it to sorted JSON and returns the string. If it is empty, it returns nothing.

**Call relations**: OutlookConnector._message_delta_pages and OutlookConnector._contact_delta_pages call this after updating folder cursors. The resulting string is passed back as the next cursor for the sync framework to store.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack workspace connector
Slack ingestion turns workspace users, channels, messages, threads, and senders into read-only synced records.

### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `source sync runs`

Slack does not hand over a whole workspace in one simple response. It gives results in pages, uses cursors to point to the next page, and sometimes reports errors inside an otherwise successful HTTP response. This file hides those Slack-specific details so the rest of the system can ask for clean streams of records.

The connector exposes five streams: users, conversations, conversation threads, messages, and message participants. Users and conversations are treated like snapshots: each sync lists everything Slack currently allows the app to see, so missing items can be marked as deleted. Messages are different. They are read channel by channel from Slack history, newest first, so a busy channel cannot cause a quiet channel to be skipped.

The file also turns Slack’s raw shapes into simpler records. For example, a Slack user profile becomes a user row with email, name, bot flags, and update time. A Slack message becomes a stable message ID, text, snippet, sender, channel, thread, and timestamp. Deleted-message events become delete markers.

A key behavior is graceful skipping. If Slack refuses access because the app lacks a permission scope, the connector records that a stream or channel was skipped instead of crashing the whole run.

#### Function details

##### `SlackApiError.__init__`  (lines 86–90)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Builds a Slack-specific error object when Slack says a request failed even though the HTTP request itself may have looked successful. It keeps Slack’s error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives a Slack error name and an optional needed permission → turns them into a readable error message → stores the raw error details on the exception for later checks.

**Call relations**: When _ok_or_raise sees Slack return ok=false, it calls this constructor. The resulting error then travels upward to code that decides whether the problem is a missing permission, a channel-specific refusal, or a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate`  (lines 98–135)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses how to read each Slack stream and yields pages of records to the sync framework. It is the main doorway the rest of the source system uses to get Slack data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor → decides whether to list users, list conversations, or walk channel histories → yields pages of normalized records or raises a skip notice for unsupported streams.

**Call relations**: The sync framework calls this when it wants data from Slack. For simple streams it hands off to iter_users or iter_conversations; for message-related streams it builds a user lookup, gathers readable channels, and uses PartitionWalk with channel_pages so each channel can be read safely over time.

*Call graph*: calls 4 internal fn (__init__, iter_conversations, iter_users, user_index); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 118–120)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Provides the list of Slack channel IDs that should be walked for message history. It is a small helper used inside message syncing.

**Data flow**: It reads the already-built channel dictionary → yields one channel ID at a time → gives PartitionWalk the set of separate channel work units.

**Call relations**: SlackConnector.paginate creates this helper after listing conversations. PartitionWalk calls on it so it can treat each channel as its own independent message-history partition.


##### `SlackConnector.paginate.channel_pages`  (lines 122–123)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects PartitionWalk’s request for one channel’s pages to the connector’s Slack history reader. It wraps the details needed to read that channel.

**Data flow**: It receives a channel ID and a time bound from PartitionWalk → looks up the channel details and passes along the stream, client, bounds, and user index → returns an async iterator over that channel’s history pages.

**Call relations**: SlackConnector.paginate gives this helper to PartitionWalk. Whenever PartitionWalk is ready to read a slice of one channel, this helper hands the work to SlackConnector._channel_pages.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 137–153)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users, page by page, and converts them into the project’s user record shape. It lets the sync know who exists in the workspace.

**Data flow**: It starts with no Slack cursor → repeatedly asks Slack users.list for a page → filters out invalid entries, flattens each user profile, yields non-empty pages, and follows Slack’s next cursor until there is no more.

**Call relations**: SlackConnector.paginate uses this for the users stream. SlackConnector.user_index also uses it to build a lookup table so later message records can show sender names and emails.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 155–195)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including channels, private channels, group messages, and direct messages. It turns Slack’s raw conversation objects into stable records the system can store.

**Data flow**: It starts with no cursor → repeatedly asks Slack conversations.list for a page → extracts names, type, privacy flags, archive status, topic, purpose, creation time, and other useful fields → yields pages until Slack has no next cursor.

**Call relations**: SlackConnector.paginate uses this directly for the conversations stream. It also uses it before message syncing to discover which non-archived channels should have their history read.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 197–204)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table of Slack users by user ID. Message syncing uses this so sender IDs can be enriched with names and email addresses.

**Data flow**: It calls iter_users and receives pages of user records → stores each record under its Slack user ID → returns a dictionary from user ID to user data.

**Call relations**: SlackConnector.paginate calls this before syncing message-related streams. The resulting user map is passed down into channel history processing and message flattening.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 206–246)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]]) -> AsyncIterator[WalkPage]
```

**Purpose**: Reads one Slack channel’s message history within a requested time window. It is careful to support both first-time backfills and later incremental reads without dropping messages.

**Data flow**: It receives a channel, a stream, a time bound, and the user lookup → posts to Slack conversations.history with the right channel, cursor, and oldest/latest limits → converts raw Slack messages into WalkPage objects and follows Slack cursors until that slice is complete. If Slack refuses that channel for an expected reason, it raises a partition skip.

**Call relations**: PartitionWalk reaches this through the channel_pages helper inside SlackConnector.paginate. It hands each raw history page to _message_page, which creates the specific records for messages, threads, or participants.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 248–290)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]]) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into records for exactly one message-derived stream. The same Slack messages can produce message rows, thread rows, or participant rows depending on which stream is being synced.

**Data flow**: It receives the stream type, conversation details, raw Slack messages, and users → skips deletion marker events except to record deleted message IDs, flattens normal messages, derives thread summaries and sender participants, and calculates the newest and oldest Slack timestamps on the page → returns a WalkPage containing records and, for the messages stream, delete markers.

**Call relations**: SlackConnector._channel_pages calls this after each conversations.history response. It delegates the detailed shaping to _flatten_message, _conversation_thread_from_message, and _participant_for_message, then gives PartitionWalk the timestamp span it needs to advance the channel cursor.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 292–312)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs top-level Slack listing calls, such as listing users or conversations, and turns missing-permission failures into clean stream skips. This prevents a permission problem from looking like a broken connector.

**Data flow**: It receives an HTTP client, Slack API path, and query parameters → calls Slack through _slack_get → returns the decoded Slack data if allowed. If Slack says the app lacks access, it raises StreamSkipped so the sync records a skip instead of a failure.

**Call relations**: iter_users and iter_conversations call this for their list requests. It relies on _slack_get to catch Slack’s ok=false style errors, then decides which refusals mean the whole stream cannot be read.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 314–317)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Runs a Slack GET request and checks Slack’s own success flag. It is a thin safety wrapper around the shared REST connector’s GET method.

**Data flow**: It receives a client, path, and optional parameters → sends the GET request through the base connector → passes the returned data to _ok_or_raise → returns only data Slack marked as successful.

**Call relations**: SlackConnector._enumerate uses this for users.list and conversations.list. It hands Slack response validation to _ok_or_raise.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 319–322)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Runs a Slack POST request and checks Slack’s own success flag. It is used for Slack calls that expect data in the request body, such as channel history reads.

**Data flow**: It receives a client, path, and optional JSON body → sends the POST request through the base connector → passes the returned data to _ok_or_raise → returns only data Slack marked as successful.

**Call relations**: SlackConnector._channel_pages uses this for conversations.history. It shares the same Slack-specific error checking as _slack_get through _ok_or_raise.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 325–330)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether Slack’s response says the operation really succeeded. This matters because Slack can return HTTP 200 while still saying ok=false inside the response body.

**Data flow**: It receives decoded Slack response data → if ok is false, extracts Slack’s error code and optional needed scope and raises SlackApiError → otherwise returns the original data unchanged.

**Call relations**: SlackConnector._slack_get and SlackConnector._slack_post call this after network requests. When it raises SlackApiError, higher-level code decides whether to skip a stream, skip one channel, or let the error fail the run.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 333–338)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s token for the next page of results. It lets loops keep reading until Slack says there are no more pages.

**Data flow**: It receives Slack response data → looks inside response_metadata.next_cursor → returns the cursor string if it exists and is not empty, otherwise returns nothing.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this after each Slack page. Its result controls whether those readers make another request or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 341–348)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts ordinary Unix timestamps into ISO-formatted UTC time strings. This makes Slack times easier for the rest of the system to compare and display.

**Data flow**: It receives any value → rejects booleans and values that cannot become a number → converts valid seconds-since-1970 into a UTC ISO string, or returns nothing for invalid input.

**Call relations**: iter_conversations uses this for conversation creation times, and _flatten_user uses it for user update times. It uses Python’s datetime conversion to produce the final string.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts_to_iso`  (lines 351–357)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack’s message timestamp format into a UTC ISO time string. Slack message timestamps are strings that look numeric, often with decimal fractions.

**Data flow**: It receives a Slack timestamp string or nothing → converts a valid timestamp to UTC ISO format → returns nothing if the input is missing or not a valid number.

**Call relations**: _flatten_message uses this for sent_at, and _conversation_thread_from_message uses it for thread update and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 360–387)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s nested user profile object into a simpler user record. It picks useful identity fields such as email, display name, real name, bot status, and update time.

**Data flow**: It receives one raw Slack member object → reads its profile section, cleans and lowercases the email, chooses the best available display names, converts update time, and sets boolean flags → returns a flat dictionary ready for storage.

**Call relations**: SlackConnector.iter_users calls this for each valid member returned by users.list. It uses _first_text to choose the best non-empty name and _unix_to_iso to normalize time.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 390–425)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into the project’s message record shape. It adds stable IDs, channel and thread links, readable sender details, text, snippet, and timing.

**Data flow**: It receives a raw message, its conversation, and the user lookup → verifies it has a Slack timestamp and channel ID → looks up the sender when possible, chooses a thread timestamp, makes a channel-plus-timestamp ID, converts the sent time, and creates a short snippet → returns the message record, or nothing if required IDs are missing.

**Call relations**: SlackConnector._message_page calls this for each non-deleted raw Slack message. It uses _slack_ts_to_iso for time, _snippet for preview text, and _first_text to choose the best sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 428–458)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread summary record when a message belongs to a real Slack thread. It ignores ordinary standalone messages because they are not conversations on their own.

**Data flow**: It receives a flattened message, the original raw message, and the conversation → checks whether the message is a thread root with replies or a thread reply → builds a thread record with title, snippet, privacy/archive flags, message count, participant count, creation time, and update time → returns the thread record or nothing.

**Call relations**: SlackConnector._message_page calls this after flattening each message. The resulting records are collected and deduplicated by thread ID before being returned for the conversation_threads stream.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 461–480)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system know who took part in a Slack message or thread.

**Data flow**: It receives a flattened message and the user lookup → finds the sender’s email or Slack user ID as a handle → if a handle exists, builds a participant record linked to the message, channel, and thread → returns that record or nothing.

**Call relations**: SlackConnector._message_page calls this for each flattened message when building the message_participants stream. It uses _first_text to choose the best sender handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 483–490)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Classifies a Slack conversation into a simple type name. This gives the rest of the system a clear label such as direct message, multi-person message, private channel, or public channel.

**Data flow**: It receives a raw Slack conversation object → checks Slack’s type flags in priority order → returns one normalized type string.

**Call relations**: SlackConnector.iter_conversations calls this while flattening each conversation. The returned value is stored on conversation records and later copied onto message records.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 493–499)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value buried inside nested dictionaries. It is used for Slack fields like topic.value and purpose.value, where missing pieces are normal.

**Data flow**: It receives a starting dictionary and a path of keys → walks one key at a time as long as the current value is still a dictionary → returns the final value, or nothing if the path cannot be followed.

**Call relations**: SlackConnector.iter_conversations uses this when extracting conversation topic and purpose. It keeps missing or oddly shaped Slack data from causing an error.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 502–506)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first useful non-empty string from several possible values. It is a small helper for picking names and handles from Slack data that may be incomplete.

**Data flow**: It receives any number of values → scans them in order → trims whitespace and returns the first non-empty string, or returns nothing if none qualify.

**Call relations**: _flatten_user uses this for display names, _flatten_message uses it for sender handles, and _participant_for_message uses it for participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 509–513)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short, tidy preview of message text. This gives search and display code a compact version of a potentially long Slack message.

**Data flow**: It receives message text or nothing → collapses repeated whitespace into single spaces → cuts the result to the configured snippet length → returns the snippet, or nothing if there is no usable text.

**Call relations**: _flatten_message calls this while building message records. The snippet can also become part of a thread title through _conversation_thread_from_message.

*Call graph*: called by 1 (_flatten_message).

# Mail, calendar, chat, and meeting source connectors  `stage-12.6`

This stage is the system’s set of communication “adapters.” It runs during the sync work, reaching out to outside services and translating their different data formats into the system’s common records, so they can be stored, searched, and recalled later.

Each file connects to one service. Calendly reads scheduling data such as event types, groups, scheduled events, and invitees. Gmail reads mailbox changes since the last run and unwraps Gmail’s nested message structure into readable text. Google Calendar imports calendar events and also records attendees separately, so the system can understand who was invited. Google Meet turns meeting transcripts and AI-generated notes into searchable pages. Microsoft Teams reads teams, channels, chats, and messages through Microsoft Graph, Microsoft’s access layer for workplace data. Outlook uses the same Microsoft Graph bridge for mail, threads, contacts, folders, and calendar events. Slack reads workspace users, channels, messages, threads, and authors. Together, these connectors act like translators at the system’s front door, turning scattered conversations and schedules into one searchable memory.

## Files in this stage

### Calendly scheduling
Reads Calendly scheduling entities and scheduled-event participation into consistent searchable records.

### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `source sync`

Calendly is organized around an account’s current organization, so this connector first asks Calendly who the current API user is and which organization they belong to. After that, most reads are scoped to that organization. This is like checking which office someone works in before collecting that office’s calendars, groups, and members.

The file defines the Calendly streams the system knows about: the API user, event types, groups, organization memberships, scheduled events, and event invitees. A stream is one kind of data the sync process can pull. The connector reads these streams through Calendly’s REST API, which means it sends web requests and receives JSON data back.

Calendly returns large collections in pages, so the connector follows Calendly’s next-page token until all pages are read. For streams that can be updated over time, it uses a saved cursor, which is a remembered timestamp or marker, to avoid rereading old data where Calendly supports it. Invitees are a special case: the connector must first read scheduled events, then ask Calendly for invitees for each event.

Finally, the file reshapes some records into easier-to-use forms. For example, scheduled events get a title, start and end time, and a simplified location. Organization memberships copy the member’s name and email out of a nested user object, then remove that nested object so later profile changes do not make the membership look changed.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like piece out of a Calendly URI. It is used when the connector needs the event UUID from a full scheduled event URI in order to ask Calendly for that event’s invitees.

**Data flow**: It receives a value that may or may not be a string. If the value is a non-empty string, it trims any trailing slash and returns the text after the last slash. If the input is missing, empty, or not a string, it returns nothing.

**Call relations**: When invitees are being synced, CalendlyConnector._invitees reads each scheduled event and calls this helper to turn the event’s full URI into the shorter event UUID needed for the invitees API path.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the authenticated user’s own account details. The connector needs this because Calendly’s organization-scoped streams require knowing the user’s current organization first.

**Data flow**: It receives an HTTP client that can make web requests. It sends a request to /users/me, looks for the resource object in the response, and returns that object if it is a dictionary; otherwise it returns an empty dictionary.

**Call relations**: CalendlyConnector._org_stream calls this before reading organization-level data, so it can add the organization to later requests. CalendlyConnector.paginate also calls it directly for the api_user stream, where the current user is the record being synced.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly collection that may be split across several pages. It hides the paging details so the rest of the connector can simply receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly asks the shared REST connector machinery for pages, telling it where Calendly puts records and where Calendly puts the next-page token. It yields each page as a list of record dictionaries.

**Call relations**: CalendlyConnector._org_stream uses this for organization-scoped collections such as event types and groups. CalendlyConnector._invitees uses it to read invitee lists for each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly collection that belongs to the current organization. It centralizes the repeated pattern of finding the organization, adding it to the request, optionally adding an update cursor, and yielding pages.

**Data flow**: It receives an HTTP client, an API path, and optionally a cursor plus the Calendly parameter name that should carry that cursor. It first reads the current user, extracts current_organization, and stops the stream with StreamSkipped if Calendly does not provide one. Then it requests the collection with the organization parameter, adds the cursor parameter when given, and yields each page with organization context attached.

**Call relations**: CalendlyConnector.paginate calls this for event types, groups, organization memberships, and scheduled events. CalendlyConnector._invitees also calls it first to get scheduled events before fetching invitees for each event. Inside, it relies on CalendlyConnector._current_user, CalendlyConnector._paginate_collection, and with_context to add useful background information to each record batch.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled events. Calendly does not expose invitees as one simple organization-wide list here, so the connector first finds events and then reads the invitees for each event.

**Data flow**: It receives an HTTP client and an optional cursor. It reads scheduled events through CalendlyConnector._org_stream, extracts each event’s UUID from its URI, and skips events that do not have a usable UUID. For each event, it requests that event’s invitees. If a cursor exists, it keeps only invitees whose created_at value is newer than the cursor. It yields non-empty invitee pages with the parent scheduled event URI and UUID added as context.

**Call relations**: CalendlyConnector.paginate calls this when the requested stream is event_invitees. This function depends on CalendlyConnector._org_stream to find events, _uuid_from_uri to build the invitees URL, CalendlyConnector._paginate_collection to walk invitee pages, and with_context to remember which event the invitees came from.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading Calendly streams. Given a stream name, it chooses the right Calendly API path and the right cursor behavior, then yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For api_user, it returns the current user as a one-record page. For organization streams, it calls CalendlyConnector._org_stream with the correct path and, where supported, the correct cursor parameter. For event_invitees, it calls CalendlyConnector._invitees. If the stream name is not one this file implements, it raises StreamSkipped.

**Call relations**: The broader source sync machinery calls this to get data for each Calendly stream. It hands work off to CalendlyConnector._current_user for the current user, CalendlyConnector._org_stream for most organization collections, and CalendlyConnector._invitees for invitees, because invitees require the extra event-by-event lookup.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into records that are easier for the rest of the system to store, compare, and search. It keeps the original fields but adds or lifts important fields into predictable places.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream, it copies useful values such as name, email, created_at, title, description, start_at, end_at, location, or api_url to top-level fields. For organization memberships, it safely reads the nested user object, copies out the person’s name and email, and removes that nested user object from the returned record. If the stream has no special rule, it returns the record unchanged.

**Call relations**: After CalendlyConnector.paginate has supplied records, the surrounding connector framework can call this before writing records into the system. This function uses dict_or_empty when reading membership users so missing or malformed nested user data does not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### Google communications
Ingests Gmail messages, Google Calendar events and attendees, and Google Meet artifacts for recallable communication context.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync runs`

Gmail does not hand over an email as one simple text field. A message is a nested MIME tree, meaning the readable parts may be buried inside smaller parts, and the text is stored in URL-safe base64, an encoded form safe for web transfer. This file is the translator between Gmail’s API and the project’s source-sync system.

The main class, GmailConnector, exposes one stream: messages. On the first run, it lists every message id in the mailbox, then fetches each message body. It also saves Gmail’s history id, which works like a bookmark. On later runs, it asks Gmail what changed after that bookmark, so it can fetch new messages and record deleted ones without rereading the whole mailbox.

The file also protects the wider sync process from common Gmail edge cases. If Gmail says the saved history id is too old, it raises a special “cursor expired” signal so the system can start fresh. If the user’s permission grant does not include Gmail reading access, it marks the stream as skipped instead of treating the whole run as broken. If a message disappears between listing and fetching, it quietly skips it.

Finally, it renders each email as readable prose: From, To, Cc, Subject, then the best available body text. If only HTML is available, it strips tags and keeps the readable words.

#### Function details

##### `GmailConnector.paginate`  (lines 75–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main doorway the sync engine uses to read Gmail messages in pages. It decides whether to do a first-time full fetch or a later change-only fetch, then returns records and deletions in chunks.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. If there is no cursor, it asks for all message ids; if there is a cursor, it asks Gmail for changes since that cursor. It fetches full message bodies for added ids, packages them into StreamPage objects, attaches deleted ids on the final page, and returns the next cursor for the next run. If Gmail refuses because the user grant lacks permission, it changes that failure into a stream skip.

**Call relations**: The sync framework calls this when it wants Gmail data. It hands first-run work to GmailConnector._backfill, later-run work to GmailConnector._history, and body downloads to GmailConnector._fetch_bodies. It then hands StreamPage results back to the core sync flow.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 109–126)

```
async def _backfill(self, client: httpx.AsyncClient) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first full read of a mailbox. It gathers every Gmail message id so the connector can fetch and store all messages from scratch.

**Data flow**: It starts with an empty list of ids and repeatedly asks Gmail’s messages endpoint for pages of message summaries. From each page, it keeps valid message ids. When there are no more pages, it asks GmailConnector._seed_history_id for a history bookmark and returns the collected ids plus that bookmark.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor yet. After collecting ids, it relies on GmailConnector._seed_history_id so future runs can switch from full rereads to smaller change-based syncs.

*Call graph*: calls 1 internal fn (_seed_history_id); called by 1 (paginate).


##### `GmailConnector._seed_history_id`  (lines 128–140)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str]) -> str | None
```

**Purpose**: This finds the Gmail history bookmark to save after a first full sync. That bookmark tells later runs where to begin looking for changes.

**Data flow**: It receives the list of message ids found during backfill. If the list is empty, it returns no cursor. Otherwise it fetches the first message in minimal form and reads its historyId. If that message vanished, it returns no cursor; otherwise it returns the history id when it is a string.

**Call relations**: GmailConnector._backfill calls this at the end of a first-time scan. Its result becomes the cursor that GmailConnector.paginate can use on the next run.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 142–177)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log from a saved history id. It identifies messages added and deleted since the previous sync.

**Data flow**: It receives an HTTP client and a saved history id. It asks Gmail’s history endpoint for pages of messageAdded and messageDeleted events, extracts message ids from those events, remembers the newest history id Gmail reports, and returns three things: ids that are currently net-added, ids deleted, and the next history bookmark. If Gmail says the old history id has expired, it raises CursorExpired so the system can refetch from scratch.

**Call relations**: GmailConnector.paginate calls this on normal incremental runs. It uses _message_ids to pull ids out of Gmail’s event records, and it signals CursorExpired when the outer sync should abandon the old cursor.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 179–193)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns a list of Gmail message ids into full, flattened message records. It is where lightweight ids become the actual email content the system can store.

**Data flow**: It receives an HTTP client and message ids. For each id, it fetches the full Gmail message. If a message no longer exists, it skips that one. For each successful response, it passes the raw Gmail shape to _flatten_message and collects the flattened records as output.

**Call relations**: GmailConnector.paginate calls this after either backfill or history has found added ids. It hands each raw Gmail response to _flatten_message so later rendering and storage do not need to understand Gmail’s nested payload format.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 195–214)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Gmail record into readable text for recall or search. Instead of dumping raw JSON, it writes something close to what a person sees when reading an email.

**Data flow**: It receives a flattened record and a stream description. For the messages stream, it reads the subject, sender, recipients, carbon-copy recipients, and body fields. It formats contact lines, chooses the best body text, and returns a title plus a plain-text document. For other streams, it falls back to the parent connector’s rendering.

**Call relations**: The sync or indexing layer calls this when it needs human-readable content. It uses _str, _format_contact, _format_recipients, and _message_body as small helpers to cleanly assemble the final email text.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 217–226)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts message ids from Gmail history event entries. Gmail wraps each id inside a small nested object, so this helper safely digs out only usable ids.

**Data flow**: It receives a value that should be a list of history entries. It ignores anything that is not shaped like the expected dictionary structure, pulls out message.id when present and non-empty, and returns a list of valid id strings.

**Call relations**: GmailConnector._history calls this while reading added and deleted events. It keeps that larger function focused on change tracking rather than the small details of Gmail’s event shape.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 229–256)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts Gmail’s raw message response into the project’s simpler message record. It lifts out the useful headers, addresses, labels, and decoded bodies.

**Data flow**: It receives the full raw Gmail message. It reads selected headers such as from, to, cc, and subject; extracts plain-text and HTML bodies; parses sender and recipient addresses; keeps labels; and derives direction as outbound when the SENT label is present. It returns one flat dictionary with these fields.

**Call relations**: GmailConnector._fetch_bodies calls this after each successful message download. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 259–273)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches an email’s nested MIME payload for readable body parts. It returns the first plain-text body and the first HTML body it can find.

**Data flow**: It receives the Gmail payload dictionary. It walks through the payload and all child parts, looking for text/plain and text/html parts with encoded body data. It decodes those parts and returns a pair: plain text if found, and HTML if found.

**Call relations**: _flatten_message calls this while building a flat record. Inside it, the nested _extract_bodies.walk function performs the actual tree traversal.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 263–270)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the recursive worker that moves through the nested email parts. Recursive means it can call the same logic on each child part, like opening boxes inside boxes.

**Data flow**: It receives one MIME part. If that part is a plain-text or HTML body and has encoded data, it decodes and stores it if that body type has not already been found. Then it visits each child part in the same way. It changes the surrounding found dictionary rather than returning its own value.

**Call relations**: _extract_bodies starts this walk at the top payload. When it finds encoded body data, it hands decoding to _b64url_decode.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 276–282)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes the way Gmail stores email body text. Gmail uses URL-safe base64, which is encoded text made safe for web addresses, and may omit padding characters.

**Data flow**: It receives an encoded string. It adds any missing padding, decodes the bytes with URL-safe base64, and turns the result into UTF-8 text, replacing broken characters if needed. If decoding fails, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this whenever it finds a text/plain or text/html body part. It relies on Python’s base64.urlsafe_b64decode for the actual decoding.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 285–292)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses the first email address from a header such as From. It separates the mailbox address from the display name.

**Data flow**: It receives a header string or nothing. If there is no header or no parsed address, it returns two empty values. Otherwise it returns the first address in lowercase and the matching display name when present.

**Call relations**: _flatten_message calls this for the sender header. It uses email.utils.getaddresses, a standard parser that understands common email address formats like “Name <person@example.com>”.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 295–302)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses a recipient header into a list of people. It is used for headers that can contain several addresses, such as To or Cc.

**Data flow**: It receives a header string or nothing. If missing, it returns an empty list. Otherwise it parses all addresses, keeps entries that have an actual address, lowercases each address, and returns dictionaries with handle and display_name fields.

**Call relations**: _flatten_message calls this for the To and Cc headers. It uses email.utils.getaddresses so the rest of the file can work with a simple list instead of raw header text.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 305–310)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This formats one person for display in rendered email text. It chooses either “Display Name <address>” or just the address.

**Data flow**: It receives a possible email handle and display name. If the handle is not a real non-empty string, it returns an empty string. If there is a display name, it combines name and address; otherwise it returns only the address.

**Call relations**: GmailConnector.render calls this for the sender. _format_recipients also calls it for each recipient so all contact formatting follows the same rule.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 313–320)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This turns a list of recipient records into one readable line. It prepares the text used after To: or Cc: in the rendered email.

**Data flow**: It receives a value that should be a list of recipient dictionaries. If it is not a list, it returns an empty string. Otherwise it formats each dictionary with _format_contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this while building the visible email header block. It delegates the formatting of each individual person to _format_contact.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 323–331)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for an email. It prefers plain text, then cleaned HTML, then Gmail’s short snippet as a last resort.

**Data flow**: It receives a flattened message record. If body_text is a non-empty string, it returns that stripped of surrounding whitespace. If not, it tries body_html and converts it to plain text with _HtmlText.extract. If neither body exists, it returns the snippet when available, or an empty string.

**Call relations**: GmailConnector.render calls this when assembling the final readable document. When HTML cleanup is needed, it hands that work to _HtmlText.extract.

*Call graph*: called by 1 (render).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only if it already is one. It prevents accidental display of non-text values where text is expected.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this for the subject before using it in the title and header text.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 342–344)

```
def __init__(self) -> None
```

**Purpose**: This prepares an HTML-to-text parser for one email body. It creates a place to collect readable text as the parser sees it.

**Data flow**: It receives no outside data besides the new object being created. It initializes the parent HTML parser with automatic character reference conversion, then creates an empty list of text parts. The result is a parser ready to be fed raw HTML.

**Call relations**: _HtmlText.extract creates this parser before feeding it HTML. The standard HTMLParser machinery then calls the parser’s data and tag methods as it reads the document.


##### `_HtmlText.extract`  (lines 347–352)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It keeps words, adds line breaks around block-like tags, and removes tags and attributes.

**Data flow**: It receives raw HTML. It creates a _HtmlText parser, feeds the HTML into it, joins the collected pieces, normalizes extra spaces on each line, removes blank lines, and returns the cleaned text.

**Call relations**: _message_body uses this when an email has HTML but no plain-text body. During parsing, HTMLParser calls _HtmlText.handle_data, _HtmlText.handle_starttag, and _HtmlText.handle_endtag.


##### `_HtmlText.handle_data`  (lines 354–355)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records the actual text found inside HTML. It is how words from the HTML body make it into the final plain-text output.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that text to the parser’s internal list. It does not return a value; it changes the parser’s collected parts.

**Call relations**: Python’s HTMLParser calls this while _HtmlText.extract is feeding raw HTML. The collected text is later joined and cleaned by _HtmlText.extract.


##### `_HtmlText.handle_starttag`  (lines 357–359)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when the parser reaches the start of a block-like HTML tag. That keeps paragraphs, list items, table cells, and headings from running together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline to the collected parts. It ignores attributes and returns nothing.

**Call relations**: HTMLParser calls this during _HtmlText.extract. Its newlines become part of the text that _HtmlText.extract later normalizes.


##### `_HtmlText.handle_endtag`  (lines 361–363)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when the parser reaches the end of a block-like HTML tag. It helps preserve the visual separation that HTML would normally show on screen.

**Data flow**: It receives the tag name. If the tag is one of the known block tags, it appends a newline to the collected parts. It returns nothing and only changes the parser’s internal text list.

**Call relations**: HTMLParser calls this during _HtmlText.extract. Together with _HtmlText.handle_starttag and _HtmlText.handle_data, it shapes HTML into readable plain text.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the rest of the system. Its job is read-only: it does not create or edit calendar events. Instead, it asks Google for events, reshapes them into the simpler record format the system stores, and reports deleted events so old records can be removed.

The main flow is incremental, meaning it tries to fetch only what changed since the last run. Think of Google giving the connector a bookmark called a sync token. On the first run there is no bookmark, so the connector looks back 90 days and asks for events from that window. Google then returns a new bookmark. On later runs, the connector sends that bookmark back and receives only new, changed, or cancelled events.

The file exposes two views of the same calendar data. The `calendar_events` stream stores one record per event, including title, time, location, description, organizer, and a folded-in list of attendee handles. The `event_attendees` stream turns each event into one row per attendee, like splitting a guest list into individual index cards.

It also knows how to react to common Google API problems. If Google's bookmark has expired, it raises a special cursor-expired signal so the wider sync system can start fresh. If the user has not granted calendar permission, it marks the stream as skipped rather than treating the whole run as broken.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Fetches Google Calendar events page by page and turns them into sync pages for the rest of the system. It supports both the main event stream and the per-attendee stream, and it understands Google's incremental sync bookmark.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is Google's saved sync token. If there is a cursor, it asks Google only for changes since that token; if not, it asks for events from the last 90 days. For each Google response page, it separates normal records from cancelled events, converts the data into the system's shape, and yields a `StreamPage` containing new or changed records, deletion IDs, and eventually the next cursor. If Google says the token expired, it raises `CursorExpired`; if permission is missing, it raises `StreamSkipped`.

**Call relations**: This is the connector's main read loop, called by the source sync machinery when it wants calendar data. During the loop it hands event records to `_flatten_event` for the `calendar_events` stream, or to `_flatten_attendees` for the `event_attendees` stream. It then passes the prepared records back to the sync system as `StreamPage` objects.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a stored calendar event record into a readable text body, suitable for search or recall. It gives events a human-friendly shape with title, time, location, attendees, and description.

**Data flow**: It receives a record and the stream it came from. For calendar event records, it reads the event fields, builds a title, and joins the useful details into a plain text block. For other streams, such as attendee rows, it falls back to the parent connector's default rendering. It returns a pair: the display title and the rendered text.

**Call relations**: The wider system calls this when it needs text to index or show for a synced record. It uses `_str` to safely treat a missing or non-text title as an empty string, then builds the final readable event summary itself.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the simpler event record the system stores. It keeps the event's main facts and folds the attendee list into the event so the event can be recalled with context about who was invited.

**Data flow**: It receives one Google event dictionary. It reads fields such as ID, creation time, update time, summary, description, location, start and end times, organizer, recurrence information, and attendees. It normalizes times through `_parse_when`, turns each attendee into a compact attendee object through `_attendee`, lowercases email handles where needed, and returns one flat event dictionary.

**Call relations**: It is called by `GoogleCalendarConnector.paginate` when the active stream is `calendar_events` and the event is not cancelled. It delegates small cleanup jobs to `_attendee` and `_parse_when`, then hands the finished record back to the pagination loop to be emitted in a sync page.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Shrinks one Google attendee object into the small attendee shape stored inside an event record. It keeps only the invitee handle, display name, and response status.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee's email address to make it a stable handle, copies the display name, translates Google's response words into the system's preferred response labels, and returns a compact dictionary.

**Call relations**: It is used by `_flatten_event` while building the attendee list inside a calendar event record. It does not talk to Google or the sync system directly; it is a cleanup helper inside the event-flattening step.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one calendar event into many attendee records, one per invitee. This lets the system store and query event attendance as separate relationship-like rows.

**Data flow**: It receives one raw Google event dictionary. It finds the event ID and organizer, then walks through the event's attendees. For each valid attendee email, it creates a stable row ID using the event ID and email handle, copies event timestamps, records the attendee's role, response, display name, and whether the attendee is the current user, and returns the full list of attendee rows.

**Call relations**: It is called by `GoogleCalendarConnector.paginate` when the active stream is `event_attendees` and the source event is not cancelled. For each attendee, it asks `_attendee_role` to decide whether the person is the organizer, a resource, optional, or required before handing the rows back to the pagination loop.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an attendee has in an event. It turns Google's boolean flags into one clear label: organizer, resource, optional, or required.

**Data flow**: It receives one attendee dictionary and a separate flag saying whether this attendee matches the organizer's email address. It checks for organizer status first, then resource, then optional, and otherwise treats the attendee as required. It returns a single role string.

**Call relations**: It is called by `_flatten_attendees` for every attendee row it creates. This keeps the role decision in one place so the attendee stream gets consistent labels.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar's two different time formats into one timestamp-like string. Google uses one shape for timed events and another for all-day events, and this function hides that difference.

**Data flow**: It receives a value that may be a Google start or end time object. If it contains `dateTime`, it returns that timestamp as text. If it contains an all-day `date`, it turns that date into midnight UTC text. If the input is missing or not in an expected shape, it returns nothing.

**Call relations**: It is called by `_flatten_event` for both the event start and end. That lets the stored event record use consistent `starts_at` and `ends_at` fields even though Google sends timed and all-day events differently.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is text. It prevents rendering code from accidentally treating non-text data as an event title.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: It is called by `GoogleCalendarConnector.render` when preparing the event title. This small guard keeps the rendered text clean even if a record is missing a title or contains an unexpected value.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `source sync / incremental ingestion`

This connector is the bridge between Google Meet and the project’s source-sync system. Google Meet stores meeting artifacts in several nested places: first a conference record, then transcript or smart-note sessions, then transcript entries, and sometimes a linked Google Docs file. This file walks that tree and turns it into one readable page per meeting.

The main flow starts by listing conference records from the Meet REST API, newest first. During incremental sync, it does not simply start exactly at the last saved time. It looks back one day, because Google may create transcripts or AI notes after the meeting has ended. That is like checking yesterday’s mailbox again because a late letter might have arrived.

For each conference, the connector fetches transcripts and smart notes. Transcript entries are copied into speaker-by-speaker dialogue. Smart notes may point to a Google Doc; when the same permission grant can read that Doc, this connector pulls out its plain text. If Google refuses access to Meet entirely, the stream is marked as skipped rather than crashed. If a linked Doc is missing or forbidden, the connector keeps the link and continues. Finally, it renders the collected data into a simple Markdown-like page with meeting metadata, transcript text, and AI summaries.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the connector’s main reader for Google Meet meeting artifacts. It asks Google for pages of conference records, fetches useful artifacts for each conference, and yields batches of finished records to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor time. It builds Google Meet API query parameters, including a one-day lookback when there is a cursor, then repeatedly downloads conference pages. For each conference it builds a richer meeting record, keeps only meetings with transcripts or smart notes, computes the next cursor from start times, and outputs a StreamPage. If Google returns a permission refusal, it changes that failure into a skipped stream message.

**Call relations**: The sync driver calls this when it wants records from the googlemeet source. It uses _lookback to avoid missing late-generated artifacts, _max_start_time to advance the cursor, and _conference_record to expand each raw conference into a useful record before handing the page back to the framework.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one raw Google Meet conference into the project’s meeting-shaped record. It gathers the conference’s transcripts and smart notes and attaches the meeting’s basic dates and identifiers.

**Data flow**: It receives the HTTP client and one conference dictionary from Google. It reads the conference resource name, asks for transcript artifacts and smart-note artifacts below that conference, converts each artifact into a cleaner record, and returns one dictionary containing the conference metadata plus the collected transcripts and smart notes.

**Call relations**: paginate calls this for every conference returned by Google. Inside, it delegates the repeated artifact-listing work to _artifacts, then sends transcript artifacts to _transcript and smart-note artifacts to _smart_note so each kind can be expanded in the right way.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all artifacts of one kind under a conference, such as all transcripts or all smart-note sessions. It hides Google’s page-by-page API format from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the artifact collection name to read. If there is no parent name, it returns an empty list. Otherwise it repeatedly calls the Meet API with page tokens, collects the artifact items from each response, and returns one combined list.

**Call relations**: _conference_record calls this twice for each meeting: once for transcripts and once for smart notes. It supplies the raw artifact lists that are then transformed by _transcript and _smart_note.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts a single Google Meet transcript session into a clean record. It includes metadata, the linked Google Docs destination if present, and the actual transcript entries.

**Data flow**: It receives the HTTP client and one transcript dictionary from Google. It extracts a stable id, state, start and end times, and any Docs link information, then calls _transcript_entries to fetch the spoken lines. It returns a dictionary ready to be embedded in a conference record.

**Call relations**: _conference_record calls this for each transcript artifact found under a conference. It uses _docs_destination for linked document details and _transcript_entries for the per-speaker text that later becomes the rendered dialogue.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual spoken entries inside a transcript. These are the small pieces that become the readable conversation in the final page.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty, it returns no entries. Otherwise it pages through Google’s transcript-entry endpoint, turns each entry into a simpler dictionary with id, participant, text, language, and times, and returns the list. If entries are forbidden or missing, it returns whatever it has collected instead of failing.

**Call relations**: _transcript calls this while building a transcript record. Later, _transcripts_section uses these entries through _dialogue to format the meeting conversation for people to read.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one Google Meet smart-note session into a clean record. If the smart note points to a Google Doc and the connector can read it, this also inlines the document’s text.

**Data flow**: It receives the HTTP client and one smart-note dictionary from Google. It extracts id, name, state, times, and any linked Docs destination. If there is a document id, it asks _document_text for the plain text and adds it as the note body when available. It returns the completed smart-note record.

**Call relations**: _conference_record calls this for each smart-note artifact under a conference. It uses _docs_destination to find the linked document and _document_text to turn that linked Google Doc into text for the final rendered AI summary section.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a linked Google Docs document and extracts its plain text. It is used so Gemini meeting notes can be searchable as text, not just stored as a link.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely encodes the id for use in a URL, downloads the document from the Docs API, and passes the returned structure to _plain_text. It returns the extracted text, or an empty string if the Doc is missing or the grant cannot read it.

**Call relations**: _smart_note calls this when a smart note has a Docs document id. It hands off the complicated Google Docs response shape to _plain_text, while keeping missing-document errors from stopping the whole meeting sync.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a collected meeting record into the final readable page text. The page includes meeting details, transcript dialogue, and AI summaries.

**Data flow**: It receives a record dictionary and a stream description. For the meeting_artifacts stream, it reads the title and key fields, builds labeled metadata, adds transcript and smart-note sections, and returns a pair: the page title and the page body. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: After paginate has produced records, the source framework uses this to make recallable prose. It calls _labeled for simple metadata, _transcripts_section for transcript text, and _smart_notes_section for AI note text.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This builds the “Transcripts” part of the rendered meeting page. It presents each transcript’s metadata and formats its entries as readable dialogue.

**Data flow**: It receives any value that should contain transcript records. It safely treats non-list or missing data as empty, then for each transcript creates labeled metadata and asks _dialogue to format the entries. It returns one text block, or an empty string when there are no transcripts.

**Call relations**: GoogleMeetConnector.render calls this while assembling the final page. It uses _labeled for transcript facts and _dialogue for the spoken conversation.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This builds the “AI summaries” part of the rendered meeting page. It shows smart-note metadata and includes the note body when it was successfully read from Google Docs.

**Data flow**: It receives any value that should contain smart-note records. It safely turns missing or invalid data into an empty list, then formats each note’s state, times, Docs link, and body text. It returns a combined text section, or an empty string if there are no notes.

**Call relations**: GoogleMeetConnector.render calls this when creating the meeting page. It relies on _labeled for the small metadata block and _str to avoid treating non-text values as text.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns many small transcript entries into a cleaner conversation. It also joins back-to-back lines from the same speaker so the output reads less choppily.

**Data flow**: It receives any value that should contain transcript entries. It skips entries without text, chooses a display name for each participant, and builds lines like “Participant: text”. When the same speaker continues on the next entry, it appends the new text to the previous line. It returns the dialogue as newline-separated text.

**Call relations**: _transcripts_section calls this while rendering each transcript. It uses _speaker to turn Google participant values into a simple name and _str to safely read text fields.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. It strips away document layout details and keeps only the text runs.

**Data flow**: It receives a Google Docs document dictionary. It walks through body content, paragraphs, paragraph elements, and text runs, collecting any text string it finds. It joins those chunks together, trims surrounding whitespace, and returns the plain text.

**Call relations**: GoogleMeetConnector._document_text calls this after downloading a document. This function does the final conversion from Google’s structured document data into simple text that can be placed in a smart-note record.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls Google Docs link information out of a transcript or smart-note artifact. It gives the rest of the connector a consistent place to find the document id and export URL.

**Data flow**: It receives an artifact dictionary. If the artifact has a docsDestination object, it extracts the document id and export link as strings and returns them in a small dictionary. If not, it returns an empty dictionary.

**Call relations**: _transcript and _smart_note call this while building their records. The returned fields are later shown in rendered output, and _smart_note may use the document id to fetch note text.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This computes the newest conference start time seen in a page of results. That value becomes the next sync cursor, which tells future runs where to resume.

**Data flow**: It receives a list of conference dictionaries and the current cursor value. It compares each conference startTime string with the current value and keeps the greatest one. It returns the updated cursor, or the original cursor if nothing newer was found.

**Call relations**: GoogleMeetConnector.paginate calls this after each page of conference records. The result is placed into the StreamPage so the sync system can remember progress.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor one day earlier. It exists because Google may generate transcripts or smart notes after a meeting ends, so a small overlap prevents missed artifacts.

**Data flow**: It receives an ISO-style timestamp string. It parses it into a date-time, subtracts the configured one-day lookback, and returns a new timestamp string formatted for Google’s API filter.

**Call relations**: GoogleMeetConnector.paginate calls this when it already has a cursor. The returned time is used in the Meet API filter so the connector refetches a recent overlap window.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the short id from a Google resource name. Google names often look like paths, and this keeps only the final piece.

**Data flow**: It receives a string such as a resource path. If the string is present, it splits on the last slash and returns the final segment. If the string is empty, it returns an empty string.

**Call relations**: Record-building functions use this whenever they need a compact id for conferences, transcripts, transcript entries, or smart notes. _speaker also uses it to turn a participant resource into a displayable name fallback.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses a readable speaker label for a transcript entry. If Google gives a participant resource, it uses the resource’s final id; otherwise it falls back to “Participant”.

**Data flow**: It receives any participant value from a transcript entry. It first keeps it only if it is a string, then extracts the final resource id. It returns that id, or the generic label “Participant” when no useful name is available.

**Call relations**: _dialogue calls this for every transcript entry that has text. Its result becomes the speaker prefix in the rendered conversation.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns text only when a value is actually a string. It prevents accidental display of non-text values like objects or nulls.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. For everything else, it returns an empty string.

**Call relations**: Many functions call this while reading Google API fields before building records or rendered text. It keeps the connector’s output predictable even when Google omits a field or sends an unexpected type.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats simple metadata lines like “start: 2024-...” while leaving out empty values. It is used to make the final page easy to scan.

**Data flow**: It receives a list of label-and-value pairs. It keeps only pairs whose value is not empty, formats each as “label: value”, joins them with newlines, and returns the resulting text block.

**Call relations**: GoogleMeetConnector.render uses this for conference metadata, while _transcripts_section and _smart_notes_section use it for artifact metadata. It provides the small, consistent label blocks throughout the rendered page.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### Microsoft Graph communications
Connects to Microsoft Graph to sync Teams conversations and Outlook mailbox, contacts, calendar, and folder data.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `source sync / request handling`

Microsoft Teams does not expose its data as local files. It must be read through Microsoft Graph, Microsoft's web API for Microsoft 365 data. This connector is the bridge between that API and the project's source-sync system.

The file defines several streams: joined teams, channels inside those teams, messages inside channels, chats, and messages inside chats. Think of it like walking a building directory: first find the departments, then the rooms inside each department, then the notes posted in each room. For chats, it first lists the chats, then visits each one for its messages.

Microsoft Graph returns data in pages, so the connector asks for one page at a time and follows Graph's "next page" links through shared REST connector behavior. For message streams, it supports incremental syncing: if the system already knows the latest saved update time, this connector only yields messages changed after that time.

It is careful about permissions and missing items. If one team, channel, or chat cannot be read because access is denied or the item disappeared, it skips that parent and continues with the rest. But if the user's grant cannot list teams or chats at all, it marks the stream as skipped rather than crashing the whole run. For messages, it also converts Microsoft Graph's HTML message body into plainer readable text.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Microsoft Teams that the signed-in user has joined. Other parts of the connector use this as the starting point before looking for channels and channel messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/joinedTeams`, collects every returned page of team records into one list, and returns that list to the caller.

**Call relations**: This is the first step for team-based syncing. `MicrosoftTeamsConnector.paginate` calls it when the requested stream is `teams`, and `MicrosoftTeamsConnector._channels` calls it before visiting each team's channels.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside every joined team. It also adds team information to each channel record so later steps know which team the channel came from.

**Data flow**: It starts with an HTTP client, calls `_teams` to get joined teams, then uses each valid team ID to ask Microsoft Graph for that team's channels. Each page of channels is enriched with context such as `team_id` and `team_name`, then yielded onward. If a particular team cannot be read or no longer exists, that team is skipped.

**Call relations**: This sits between team discovery and message discovery. `MicrosoftTeamsConnector.paginate` calls it for the `channels` stream, while `MicrosoftTeamsConnector._channel_messages` calls it so it can visit each channel and fetch its messages.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from every accessible Teams channel. It can limit the output to messages modified after a saved cursor, which keeps later syncs from rereading old data.

**Data flow**: It receives an HTTP client and an optional cursor, which is a stored timestamp-like value from the last sync. It gets channels from `_channels`, then asks Microsoft Graph for messages in each channel. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is newer. It adds context such as `team_id`, `channel_id`, and `thread_id`, then yields non-empty pages of messages.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when syncing the `channel_messages` stream. It depends on `_channels` to know where to look, and it uses `with_context` so downstream storage can connect each message back to its team and channel.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of chats visible to the signed-in user. This is the starting point for syncing direct or group chat messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/chats`, gathers all pages of chat records into a list, and returns that list.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls it when the requested stream is `chats`. `MicrosoftTeamsConnector._chat_messages` also calls it before visiting each chat to fetch messages.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from each accessible Microsoft Teams chat. Like channel message syncing, it can use a cursor so only newly changed chat messages are returned.

**Data flow**: It receives an HTTP client and an optional cursor. It first gets chats from `_chats`, then uses each valid chat ID to request that chat's messages. If a cursor is supplied, it filters out messages whose `lastModifiedDateTime` is not newer. It adds `chat_id` and `thread_id` context and yields only pages that still contain messages. If one chat cannot be read or has disappeared, it skips that chat and continues.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this for the `chat_messages` stream. It builds on `_chats` for the list of parent chats and hands enriched message pages back to the sync runner.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Routes a requested stream name to the right Microsoft Teams fetching routine. This is the main method the generic source-sync framework calls when it wants records from this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it calls the matching helper: teams, channels, channel messages, chats, or chat messages. It yields pages of records back to the framework. If Microsoft Graph refuses access with an authorization-related status, it turns that into a `StreamSkipped` result so the run records a permission skip instead of treating it as a broken connector.

**Call relations**: This is the dispatcher for the file. The wider REST source framework calls `paginate`; `paginate` then hands the work to `_teams`, `_channels`, `_channel_messages`, `_chats`, or `_chat_messages` depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into text suitable for a recallable page. For messages, it extracts the subject and body and removes basic HTML tags from the body.

**Data flow**: It receives one record and the stream it belongs to. If the record is not a channel or chat message, it lets the base REST connector render it in the standard way. For message records, it reads the subject, pulls `body.content` from the nested record, strips HTML tags, and returns a title plus a readable text document with a heading and body.

**Call relations**: The sync framework uses this after records have been fetched and need to become searchable or displayable content. It calls `_str` to safely read the title and `_strip_html` to make Microsoft Graph's HTML message body easier to read.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a simple HTML string into plainer text by removing tags. This matters because Microsoft Graph stores Teams message bodies as HTML, while recall text should be readable.

**Data flow**: It receives any value. If the value is not a string, it returns `None`. If it is a string, it replaces anything that looks like an HTML tag with a space, trims the result, and returns the cleaned text.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when preparing channel and chat messages for display or search. It is a small helper used only in this file.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a possible title value into a string without inventing text for non-string values. It prevents unexpected data shapes from leaking into message titles.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. It keeps rendering simple and predictable even when Microsoft Graph leaves the subject missing or returns an unexpected type.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `source sync runs`

Outlook data lives behind Microsoft Graph, Microsoft’s web API for mail, calendars, contacts, and other account data. This file teaches the source system how to ask Graph for Outlook records, how to keep track of where a previous sync stopped, and how to turn Graph’s raw shapes into fields the rest of the product can use. Most streams use Graph’s “delta” feed, which is like asking, “What changed since my last visit?” The first sync walks through pages of results and saves a special link as the cursor. Later syncs reuse that link so Graph returns only new, changed, or deleted items. Messages and contacts are more complicated because they live in folders, so their cursor is a small JSON map from folder ID to that folder’s delta link. Conversations are not a separate Graph object here. They are built by reading messages and grouping them by conversation ID, like sorting letters into thread piles. The connector also tidies records: it picks the first contact email, chooses a phone number, strips HTML from event descriptions, and adds friendly fields like subject, snippet, sender, and start time. If Microsoft refuses access with a permission error, the stream is marked skipped instead of crashing the whole run.

#### Function details

##### `_strip_html`  (lines 33–36)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a text value so an event description can be shown as plain text. If the value is not text, it returns nothing instead of guessing.

**Data flow**: It receives any value. If the value is a string, it replaces anything that looks like an HTML tag with spaces and trims the result; otherwise it returns null. The output is either cleaned text or null.

**Call relations**: OutlookConnector.flatten uses this when preparing calendar event records, because Microsoft Graph may store an event body as HTML but the rest of the system wants a readable plain-text description.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 39–47)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact. This gives contacts a simple top-level email field even though Graph stores email addresses inside a list of nested objects.

**Data flow**: It receives one contact record. It looks at the record’s emailAddresses list, checks each entry for emailAddress.address, and returns the first non-empty string it finds. If the structure is missing or empty, it returns null.

**Call relations**: OutlookConnector.flatten calls this while turning raw contact records into easier-to-search records. It relies on get_path to safely read a nested field without failing if part of the path is missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 50–60)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses one useful phone number for a contact. It prefers the mobile phone, then falls back to the first business phone number.

**Data flow**: It receives one contact record. It first checks mobilePhone for a non-empty string; if that is absent, it scans businessPhones for the first non-empty string. It returns the chosen phone number or null.

**Call relations**: OutlookConnector.flatten calls this while simplifying contact records, so downstream code can look at one phone field instead of understanding Outlook’s separate mobile and business phone fields.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate`  (lines 110–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right reading strategy for each Outlook stream and yields pages of records to the sync framework. It is the connector’s main doorway for fetching Outlook data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. Based on the stream name, it delegates to the matching helper for conversations, messages, contacts, events, or mail folders. It yields the pages produced by that helper. If Microsoft Graph returns a 401 or 403 permission error, it turns that into a skipped stream instead of a hard failure.

**Call relations**: The source framework calls paginate when it wants records for a stream. paginate then hands off to _conversation_pages, _message_delta_pages, _contact_delta_pages, _event_delta_pages, or _graph_delta_pages. If no known stream matches, it raises StreamSkipped to tell the run that this stream is not available here.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages).


##### `OutlookConnector._conversation_pages`  (lines 145–183)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds email conversation records by reading messages and grouping them by Microsoft’s conversationId. This creates a thread view even though the connector reads individual messages from Graph.

**Data flow**: It receives an HTTP client and an optional timestamp cursor. It asks Graph for messages, optionally only those modified after the cursor, then groups them by conversationId. For each group it keeps a title, subject, snippet, first seen time, last message time, and latest update time. It yields one list of conversation records if any were found.

**Call relations**: OutlookConnector.paginate calls this when the requested stream is conversations. Unlike the delta helpers, this reads messages in modified-time order and collapses them into conversation summaries before handing them back.

*Call graph*: called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 185–222)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta endpoint and turns each response into a standard StreamPage. A delta endpoint reports both changed records and deleted record IDs since the saved cursor.

**Data flow**: It receives an HTTP client, an initial Graph path, an optional cursor link, and optional query parameters. It starts from the cursor if present, otherwise from the initial path. For each Graph response, it separates normal records from items marked @removed, captures the next page link or final delta link as the next cursor, and yields a StreamPage containing records, deletes, and the next cursor. It continues while Graph provides a next-page link.

**Call relations**: This is the shared engine used by mail folders directly through paginate and by the message, contact, and event helpers. Those helpers add stream-specific setup, while _graph_delta_pages does the common delta-feed work and creates StreamPage objects.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 224–244)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook messages across all mail folders. It keeps a separate saved position for each folder because Microsoft Graph tracks message deltas per folder.

**Data flow**: It receives an HTTP client and a JSON cursor map from earlier syncs. It decodes that map, lists the user’s mail folders, then reads each folder’s messages delta feed. As pages arrive, it adds the folder ID onto each message, updates that folder’s cursor, re-encodes the cursor map, and yields StreamPage objects with records, deletes, and the updated combined cursor.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. This function calls _list_mail_folders to discover folders, _decode_cursor_map and _encode_cursor_map to preserve folder-specific progress, and _graph_delta_pages to do the actual Graph delta reading.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 246–273)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook contacts from the default contacts area and from contact folders. Like messages, contacts need folder-by-folder cursors.

**Data flow**: It receives an HTTP client and an optional JSON cursor map. It decodes the saved folder cursors, builds a folder list containing the default contact area plus named contact folders, and reads each folder’s contacts delta feed. After each page, it updates that folder’s cursor and yields a StreamPage with the latest combined cursor. If the default contacts delta endpoint is missing or unsupported, it quietly skips that default area for certain Graph errors.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It calls _list_contact_folders to discover contact folders, _graph_delta_pages to read each delta feed, and the cursor encoding helpers to save progress across folders.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 275–286)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed calendar events in a broad time window around the current date. This keeps the sync focused on events that are likely to matter, rather than asking for an unbounded calendar history.

**Data flow**: It receives an HTTP client and an optional cursor. It calculates a date range from one year in the past to two years in the future, passes that range to Graph’s calendarView delta endpoint, and yields the StreamPage objects returned by the shared delta reader.

**Call relations**: OutlookConnector.paginate calls this for the events stream. It uses _graph_delta_pages for the common delta-feed paging and only adds the calendar-specific date range.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 288–295)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s Outlook mail folders. Message syncing needs these IDs because Graph exposes message changes separately inside each folder.

**Data flow**: It receives an HTTP client. It pages through /me/mailFolders, reads each folder’s id field, and returns a list of non-empty folder ID strings.

**Call relations**: _message_delta_pages calls this before reading message delta feeds. The returned folder IDs become the set of per-folder message feeds to sync.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 297–304)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s Outlook contact folders. Contact syncing uses these IDs to read contacts from each folder separately.

**Data flow**: It receives an HTTP client. It pages through /me/contactFolders, extracts valid id fields, and returns them as a list of strings.

**Call relations**: _contact_delta_pages calls this after accounting for the default contacts area. The IDs it returns are used to build each folder’s contacts delta path.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 306–336)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into friendlier records with common field names. This makes Outlook data easier for the rest of the system to index, search, or display.

**Data flow**: It receives one raw record and the stream it belongs to. For contacts, it adds fields such as first_name, last_name, name, email, phone, and created_at. For messages, it adds subject, snippet, sender address, sent time, conversation ID, and thread ID. For events, it adds title, plain-text description, start and end times, and location. For other streams, it returns the record unchanged.

**Call relations**: The connector framework uses flatten after records are fetched. Inside this file it calls _first_email and _phone for contacts, _strip_html for event descriptions, and get_path to safely read nested Graph fields such as sender email and event location.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 339–348)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Turns a saved JSON cursor map back into a normal dictionary. This is used when one stream needs many saved positions, such as one cursor per folder.

**Data flow**: It receives a raw cursor string or null. If the cursor is missing, not valid JSON, or not a dictionary, it returns an empty dictionary. Otherwise it keeps only entries whose values are non-empty strings, converting keys to strings too.

**Call relations**: _message_delta_pages and _contact_delta_pages call this at the start of a sync so they can resume each folder from its own saved Microsoft Graph delta link.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 351–352)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns a folder-to-cursor dictionary into a JSON string that can be saved as the stream cursor. This lets the next sync resume many folder feeds from one stored value.

**Data flow**: It receives a dictionary of folder IDs to cursor links. If the dictionary has entries, it serializes it as sorted JSON; if it is empty, it returns null. The output is the cursor string saved for later.

**Call relations**: _message_delta_pages and _contact_delta_pages call this after receiving new per-folder delta links from Graph, so each yielded StreamPage carries an up-to-date combined cursor.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack workspace chat
Reads Slack workspace users, conversations, messages, threads, and authors into searchable records.

### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `source sync`

Slack does not hand over a whole workspace in one simple download. It sends results in pages, uses special cursor tokens to ask for the next page, and often reports errors inside a normal-looking response. This file wraps those Slack-specific rules so the rest of the source-sync system can treat Slack like a set of named streams: users, conversations, messages, threads, and message participants.

The connector first lists users and conversations as full snapshots. That means if a user or channel disappears from what the Slack token can see, the sync can mark it as gone. Messages are more delicate. Slack history is read one conversation at a time, newest first, so this file uses a partitioned walk: each channel gets its own place marker, like separate bookmarks in separate books. A busy channel can move forward without causing quiet channels to be skipped.

For each raw Slack message page, the file filters out the connector’s own live bot user, notices deleted-message events, and creates three useful shapes: message records, thread records, and participant records. It also distinguishes between a whole stream being unreadable because the Slack grant lacks permission, and one channel being unreadable. A missing permission for all users or channels becomes a recorded skip, while one refused channel is skipped without failing the rest of the workspace sync.

#### Function details

##### `SlackApiError.__init__`  (lines 89–93)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a clear error object for Slack API failures that arrive as a normal HTTP response but contain `ok=false`. It keeps Slack’s short error code, and optionally the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives Slack’s error code and possibly a needed permission name. It builds a readable message like “slack: missing_scope” and stores the raw code and needed scope on the error object. The result is an exception that carries both human-readable text and machine-checkable details.

**Call relations**: When `_ok_or_raise` sees Slack report failure inside a response body, it calls this constructor. The higher-level connector methods then inspect this error to decide whether the sync should skip a stream, skip one channel, or raise a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 101–114)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides the standard entry point that the source-sync framework uses to ask this connector for pages of Slack records. It simply adapts the framework call into the connector’s main Slack pagination routine.

**Data flow**: It receives an HTTP client, the stream being requested, an optional saved cursor, and the connector’s own Slack user id. It passes those values into `SlackConnector.paginate`. What comes out is an async stream of record pages or stream pages that the sync framework can consume.

**Call relations**: The wider sync framework calls this method when it wants data from Slack. This method immediately hands the work to `SlackConnector.paginate`, keeping the public connector interface small and consistent with other sources.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 116–165)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right reading strategy for each Slack stream. Users and conversations are simple paged lists, while messages, threads, and participants are derived from per-channel history walks.

**Data flow**: It receives the stream name, a saved cursor, and optional self-user id. For users it yields pages from `iter_users`; for conversations it yields pages from `iter_conversations`; for message-related streams it first builds a user lookup and channel list, then walks each channel’s history with `PartitionWalk`. It outputs pages of normalized records, or raises a skip when the stream is not implemented.

**Call relations**: `paginate_source` calls this as the main dispatcher. It calls `iter_users`, `iter_conversations`, and `user_index` for setup, then creates a `PartitionWalk` that repeatedly asks the nested `channel_pages` helper for one channel’s history pages.

*Call graph*: calls 4 internal fn (__init__, iter_conversations, iter_users, user_index); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 141–143)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies the list of channel ids that should be walked for message history. It is a small async helper because the partition walker expects an async source of partition names.

**Data flow**: It reads the channel ids already collected by `paginate`. It yields each id one by one. It does not change data; it just turns the channel map into a stream of channel bookmarks.

**Call relations**: `SlackConnector.paginate` gives this helper to `PartitionWalk`. The walk uses it to know which channels have independent history cursors.


##### `SlackConnector.paginate.channel_pages`  (lines 145–153)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the partition walker to the Slack history reader for one channel. It tells the walker how to fetch pages for a specific channel and time window.

**Data flow**: It receives a channel id and a partition bound, which says whether to read newer than or older than a saved point. It looks up the channel details and passes the client, stream, conversation, bound, user lookup, and self-user id into `_channel_pages`. It returns the async pages produced there.

**Call relations**: `SlackConnector.paginate` gives this helper to `PartitionWalk`. Whenever the walk is ready to read a channel, it calls this helper, which hands off the actual Slack API work to `_channel_pages`.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 167–183)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all Slack users visible to the token and turns them into the project’s user record shape. It follows Slack’s page cursor until there are no more users.

**Data flow**: It starts with no cursor, calls Slack’s users list endpoint through `_enumerate`, flattens each valid raw member with `_flatten_user`, yields a page if it contains users, then reads the next cursor with `_next_cursor`. The output is a sequence of user-record lists.

**Call relations**: `SlackConnector.paginate` uses this when the requested stream is `users`. `SlackConnector.user_index` also uses it to build a lookup table needed while converting messages into richer records.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 185–225)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all Slack conversations visible to the token, including public channels, private channels, group messages, and direct messages. It keeps archived conversations too, so the sync can notice their archived state.

**Data flow**: It requests Slack conversation pages through `_enumerate`, extracts stable fields like id, name, type, privacy flags, creation time, topic, and purpose, and yields each non-empty page. It uses `_next_cursor` to continue until Slack has no next page.

**Call relations**: `SlackConnector.paginate` calls this directly for the conversations stream and also uses it before message syncing to discover which non-archived channels should be read. It relies on helper functions to classify conversation type, pull nested text, and convert timestamps.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 227–234)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a dictionary of Slack users keyed by user id. Message conversion uses this to attach names and email addresses to message authors.

**Data flow**: It reads every page from `iter_users`. For each user record with a string id, it stores that record under the id. The output is a lookup table from Slack user id to normalized user data.

**Call relations**: `SlackConnector.paginate` calls this before reading message-related streams. `_message_page` and its helpers then use the resulting user map to enrich messages and participants.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 236–283)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one channel’s message history from Slack within the time window chosen by the partition walker. It is careful to continue correctly whether the sync is catching up on old messages or only reading new ones.

**Data flow**: It receives a conversation, a stream definition, a time bound, a user lookup, and the connector’s own user id. It sends POST requests to Slack’s conversation history endpoint, adds `oldest` or `latest` parameters according to the bound, filters raw messages that have timestamps, and converts each page through `_message_page`. It yields walk pages that include records plus the highest and lowest Slack timestamps seen.

**Call relations**: The nested `channel_pages` helper in `paginate` calls this for each channel selected by `PartitionWalk`. If Slack refuses this particular channel, it raises `PartitionSkipped` so the walk can skip that channel and continue with others.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 285–333)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into whichever message-related stream was requested: threads, messages, or participants. It also records deleted Slack messages so the message stream can tombstone them.

**Data flow**: It receives raw Slack messages, conversation details, user details, and the target stream. It skips Slack deletion notices after extracting their deleted message ids, flattens normal messages, derives possible thread records and participant records, and calculates the timestamp span of the page. It returns a `WalkPage` containing the records for the requested stream, and for messages may include delete ids.

**Call relations**: `_channel_pages` calls this after each successful Slack history response. It delegates the record shaping to `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message`, then hands the finished page back to the partition walk.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 335–355)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Wraps top-level Slack list calls such as users and conversations with permission-aware error handling. If Slack says the token lacks permission for an entire list, the sync records the stream as skipped instead of treating the whole run as broken.

**Data flow**: It receives an HTTP client, an API path, and query parameters. It calls `_slack_get`; if Slack reports a known permission refusal, or the HTTP status is a known refusal such as 403, it converts that into `StreamSkipped`. Otherwise it returns the successful response data or lets unexpected errors rise.

**Call relations**: `iter_users` and `iter_conversations` call this for their list endpoints. It relies on `_slack_get` to perform the request and normalize Slack’s `ok=false` responses into `SlackApiError`.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 357–360)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a Slack GET request and checks Slack’s success flag. This keeps Slack’s unusual error style out of the higher-level reading code.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It calls the base REST connector’s GET method, then passes the returned JSON-like data to `_ok_or_raise`. It returns the data only if Slack marked it as successful.

**Call relations**: `_enumerate` calls this for Slack list endpoints. `_ok_or_raise` may turn a Slack body-level error into `SlackApiError`, which `_enumerate` can interpret.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 362–365)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a Slack POST request and checks Slack’s success flag. It is used for conversation history because Slack’s history read shape is a POST in this connector.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It calls the base REST connector’s POST method, then sends the response data through `_ok_or_raise`. It returns successful Slack data or raises a Slack-specific error.

**Call relations**: `_channel_pages` calls this while walking message history for each channel. If `_ok_or_raise` raises a channel permission error, `_channel_pages` can convert it into a skipped partition.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 368–373)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether a Slack response body says the request really succeeded. Slack can send HTTP 200 while still saying `ok=false`, so this helper is the guardrail that catches those hidden failures.

**Data flow**: It receives response data as a dictionary. If `ok` is false, it extracts Slack’s error code and optional needed scope and raises `SlackApiError`. If Slack did not report failure, it returns the same data unchanged.

**Call relations**: `_slack_get` and `_slack_post` call this after network requests. When it raises `SlackApiError`, callers higher up decide whether that means skip the stream, skip one channel, or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 376–381)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s next-page token in a response. This is how the connector knows whether to keep asking Slack for more results.

**Data flow**: It receives a Slack response dictionary. It looks inside `response_metadata.next_cursor`, checks that it is a non-empty string, and returns it. If no usable cursor is present, it returns nothing.

**Call relations**: `iter_users`, `iter_conversations`, and `_channel_pages` call this after each Slack page. A returned cursor makes those loops continue; no cursor ends the current listing or channel history walk.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 384–391)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts ordinary Unix timestamps into ISO date strings, the common readable timestamp format used in records. It refuses booleans and invalid values so bad Slack data does not become misleading dates.

**Data flow**: It receives any value that might represent seconds since 1970. It tries to turn it into a number and then into a UTC timestamp string. It returns the ISO string on success, or nothing if the input is missing or invalid.

**Call relations**: `iter_conversations` uses this for channel creation time, and `_flatten_user` uses it for user update time. It calls the standard datetime conversion function to do the actual time translation.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts_to_iso`  (lines 394–400)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack message timestamps into ISO date strings. Slack message timestamps look like decimal strings, so this helper handles that special format.

**Data flow**: It receives a Slack timestamp string or nothing. If present and valid, it converts the decimal seconds into a UTC ISO timestamp. If the value is missing or malformed, it returns nothing.

**Call relations**: `_flatten_message` uses this for message sent time, and `_conversation_thread_from_message` uses it for thread creation, update, and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 403–430)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s raw user object into a cleaner user record for the sync system. It pulls useful profile fields into predictable top-level fields.

**Data flow**: It receives one raw Slack member dictionary. It reads the nested profile if present, normalizes email to lowercase, chooses the best display and real names, copies flags such as bot or deleted, and converts update time. It returns a normalized user dictionary.

**Call relations**: `iter_users` calls this for every valid member from Slack. It uses `_first_text` to choose the first usable name and `_unix_to_iso` to format Slack’s update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 433–474)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into the project’s message record shape. It also filters out messages sent by the connector’s own Slack user so the sync does not ingest its own live bot output.

**Data flow**: It receives a raw message, conversation details, the user lookup, and optional self-user id. It verifies the message and channel have ids, skips the self user, finds author details, chooses the thread id, formats the sent time, builds a short snippet, and returns a normalized message record. If the message cannot safely be represented, it returns nothing.

**Call relations**: `_message_page` calls this for each raw non-deletion message. It relies on `_slack_ts_to_iso`, `_snippet`, and `_first_text` to make stable, readable fields for later thread and participant derivation.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 477–507)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Derives a thread record from a message when that message is part of a Slack thread. It ignores ordinary standalone messages that are not thread roots or replies.

**Data flow**: It receives the normalized message, the original raw message, and conversation details. It checks whether the message starts a thread with replies or is itself a reply, then builds a thread record with title, counts, channel details, and timestamps. If there is no real thread, it returns nothing.

**Call relations**: `_message_page` calls this after flattening each message. The returned thread records are collected by id, and the most recently updated version is kept for the `conversation_threads` stream.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 510–530)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system know who took part in a message or thread, not just what the text said.

**Data flow**: It receives a normalized message and the user lookup. It finds the sender’s best handle, preferring email when available, and builds a record linking that handle to the message, channel, and thread. If no usable sender handle exists, it returns nothing.

**Call relations**: `_message_page` calls this for each flattened message. It uses `_first_text` to choose a usable handle, and its results feed the `message_participants` stream.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 533–540)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Classifies Slack’s many conversation flags into one simple conversation type. This gives downstream records a single readable type such as direct message, multi-person message, private channel, or public channel.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s boolean flags in priority order and returns the matching type string. The output is a compact label used in normalized conversation and message records.

**Call relations**: `iter_conversations` calls this while building each conversation record. The resulting type is also carried into message records through the conversation details.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 543–549)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value buried inside nested dictionaries, such as a channel topic’s text. It avoids crashes when Slack leaves part of the nested structure out.

**Data flow**: It receives a dictionary and a path of keys. It walks through the keys one at a time; if the current value is not a dictionary before the path ends, it returns nothing. Otherwise it returns the final value found.

**Call relations**: `iter_conversations` uses this to read conversation topic and purpose text. It keeps that code simple and safe when Slack’s response shape varies.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 552–556)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from a list of possibilities. It is used whenever Slack offers several possible names or handles and the connector wants the best available one.

**Data flow**: It receives any number of values. It scans them in order, returns the first string that still has text after trimming spaces, and returns nothing if none qualify.

**Call relations**: `_flatten_user`, `_flatten_message`, and `_participant_for_message` call this to pick display names, sender handles, and participant handles without repeating the same fallback logic.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 559–563)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short preview of message text. This gives search and thread records a compact summary without storing a long or oddly spaced preview field.

**Data flow**: It receives message text or nothing. It collapses repeated whitespace into single spaces, cuts the result to the configured snippet length, and returns the preview. If there is no real text after cleanup, it returns nothing.

**Call relations**: `_flatten_message` calls this while building message records. The snippet is later reused by `_conversation_thread_from_message` as a possible thread title or summary.

*Call graph*: called by 1 (_flatten_message).

# Workspace Content, Communication, and Document Providers  `stage-16.5`

This stage is shared behind-the-scenes support for bringing a user’s work content into the system. Each file is a “provider,” meaning a reader that talks to an outside service, collects allowed data, and reshapes it into the project’s common searchable format. Gmail reads mail and tracks changes so future syncs can fetch only updates. Outlook does the same kind of bridge for Microsoft mail, contacts, calendars, conversations, and folders. Google Calendar reads events and attendees. Google Docs, Sheets, Drive, and Meet read documents, spreadsheets, files, permissions, comments, revisions, transcripts, and meeting notes. The shared Google helper tells permission errors apart from quota limits, so the sync knows when to skip and when to retry. Slack reads users, channels, messages, and threads. Microsoft Teams reads teams, channels, chats, and messages through Microsoft Graph. Confluence reads spaces, pages, blog posts, comments, groups, and audits. Notion reads users, pages, data sources, comments, and nested page blocks. Together, these adapters act like translators for many workplace tools.

## Files in this stage

### Confluence knowledge spaces
Reads Atlassian Confluence spaces, pages, blogs, comments, groups, and audit entries into searchable workspace text.

### `extensions/sources/ufo_ext_sources/providers/confluence.py`

`io_transport` · `source sync`

Confluence is a wiki and documentation tool, but its page bodies are not stored as simple text. They are stored as XHTML, which is HTML-like markup with tags for headings, tables, macros, and other page structure. If the system saved that raw markup, search or recall would return noisy text full of tags instead of something a person would recognize. This file solves that by fetching Confluence records and reshaping them into useful records with clear titles, body text, dates, authors, links, and stable IDs.

The connector first asks Atlassian which Confluence sites the current permission grant can reach. A single grant may cover more than one site, so each stream is run separately for each site. It then calls the correct Confluence API endpoint, page by page, using Confluence's start-and-limit paging style. Some streams are incremental, meaning the connector compares each record's date-like cursor against the last saved watermark and only lets newer records through.

The file also protects the sync from common permission problems. If Confluence says access is unauthorized or forbidden, the stream is marked as skipped rather than crashing the whole run. Finally, the custom renderer strips Confluence storage-format markup down to readable prose, like turning a web page's source code back into the words a reader sees.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: This helper pulls the readable body field out of a Confluence record. It looks first for the storage-format body and then for the view-format body, because Confluence may provide page text in either place.

**Data flow**: It receives one record as a dictionary-like object. It reads the nested body text path, checks that the value is a non-empty string, and returns that string; if there is no usable body text, it returns nothing.

**Call relations**: During record shaping, `ConfluenceConnector.flatten` calls this helper when preparing pages, blog posts, and comments. It supplies the body text that later rendering can clean up into recallable prose.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Confluence stream. It finds the right API path, visits every Confluence site available to the grant, and yields batches of records for the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It maps the stream name to a Confluence API path, asks for accessible sites, reads each site's records page by page, adds site context such as `cloud_id` and `site_url`, and yields lists of records. If Confluence refuses access with a permission-related response, it turns that into a skipped stream instead of a hard failure.

**Call relations**: The broader source framework calls this when it needs records for one stream. This method relies on `_sites` to discover Confluence sites, `_offset_results` to walk through records inside each site, and `with_context` to attach site information that later functions need for IDs and URLs.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Confluence sites the current authorization grant can access. That matters because all later Confluence API calls must be scoped to one specific site.

**Data flow**: It receives an HTTP client. It calls Atlassian's accessible-resources endpoint, reads the JSON response if there is content, and returns it as a list; if the response is missing or not list-shaped, it safely produces an empty list.

**Call relations**: `ConfluenceConnector.paginate` calls this before reading any stream data. The site IDs returned here become the `cloud_id` values used to build the per-site Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: This walks through one Confluence collection, one page of API results at a time. It also applies incremental filtering locally when a stream has a cursor, because Confluence does not provide a server-side "only since this date" option here.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and optional cursor information. It repeatedly requests records using `start` and `limit`, pulls the `results` list from each response, removes records whose cursor value is not newer than the saved cursor, yields non-empty batches, and stops when there are no records or Confluence does not provide a next-page link.

**Call relations**: `ConfluenceConnector.paginate` calls this once per stream per site. It hands batches back upward so `paginate` can add site context before the sync engine receives them.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Confluence API records into a more consistent form the rest of the system can index and recall. It gives different stream types the fields that matter most, such as page title, body, URL, author, and created or updated times.

**Data flow**: It receives a raw record and its stream description. It reads nested values such as web links, version dates, body text, author IDs, and parent page IDs, then builds a flatter dictionary. When possible, it prefixes record IDs with the Confluence `cloud_id` so records from different sites do not collide; for audit entries, it leaves the ID alone because the ID is also the cursor used for ordering.

**Call relations**: The source framework uses this after records are fetched and before they are stored or rendered. It calls `_body_text` for page-like body fields and uses nested-path lookups so later stages can work with simple, predictable fields.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Confluence records into readable text for recall. It avoids dumping raw JSON or raw XHTML and instead produces a simple title plus human-readable body text.

**Data flow**: It receives a flattened record and stream description. For pages, blog posts, comments, and spaces, it chooses a title, extracts readable text from the body or description, builds a heading, and returns both the title and rendered text. For other streams, it falls back to the base connector's normal rendering behavior.

**Call relations**: The source framework calls this when it needs the text representation of a synced record. It uses `_StorageTextExtractor.extract` to clean Confluence markup and `_str` to safely treat only real strings as titles.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: This prepares a small HTML parser used to collect readable text from Confluence's storage-format XHTML. It starts with an empty list where text pieces and line breaks will be gathered.

**Data flow**: It receives no outside data beyond the new parser object being created. It initializes the standard HTML parser with automatic character-reference conversion, then creates an empty internal list for collected text parts.

**Call relations**: `_StorageTextExtractor.extract` creates an instance of this class whenever it needs to clean a raw Confluence body. The rest of the parser methods add content to the internal list that this initializer sets up.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: This is the simple public doorway for turning Confluence XHTML into plain text. Callers do not need to know how the parser works; they pass raw content in and get cleaned text back.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise, it creates a parser, feeds the raw markup into it, asks the parser to assemble the cleaned text, and returns that result.

**Call relations**: `ConfluenceConnector.render` uses this when rendering page bodies, blog post bodies, comment bodies, and space descriptions. It coordinates the parser's lower-level callbacks such as `handle_data`, `handle_starttag`, `handle_endtag`, and `_text`.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This parser callback records actual readable text found between markup tags. It is the part that keeps the words a Confluence reader would see.

**Data flow**: It receives a text fragment from the HTML parser. It appends that fragment to the parser's internal list of parts, changing the parser's accumulated state but returning no separate value.

**Call relations**: The Python HTML parser calls this automatically while `_StorageTextExtractor.extract` feeds it markup. Later, `_text` combines everything collected here into the final plain-text body.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This parser callback adds a line break when a block-like HTML tag begins. That keeps paragraphs, headings, table cells, and list items from being mashed together.

**Data flow**: It receives a tag name and its attributes. If the tag is one of the known block-style tags, it appends a newline marker to the internal text parts; otherwise it ignores the tag and all attributes.

**Call relations**: The HTML parser calls this during `_StorageTextExtractor.extract`. Its newline markers are later cleaned up by `_text`, giving rendered Confluence text readable spacing without preserving the raw tags.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This parser callback adds a line break when a block-like HTML tag ends. It helps preserve natural boundaries between sections of text.

**Data flow**: It receives a tag name. If the tag is in the block-tag set, it appends a newline marker to the parser's internal parts; otherwise it does nothing.

**Call relations**: The HTML parser calls this as markup is fed through `_StorageTextExtractor.extract`. Together with `handle_starttag`, it gives `_text` enough boundaries to produce clean lines instead of one long run-on sentence.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: This assembles the parser's collected pieces into the final clean plain text. It removes extra whitespace and empty lines so the output is compact but still readable.

**Data flow**: It joins all collected text and newline markers into one string. It splits that string into lines, normalizes repeated spaces inside each line, drops blank lines, joins the remaining lines with newline characters, trims the ends, and returns the result.

**Call relations**: `_StorageTextExtractor.extract` calls this after all markup has been parsed. It is the final cleanup step before rendered text is handed back to `ConfluenceConnector.render`.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely returns a value only if it is already a string. It prevents non-text values from accidentally becoming titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: `ConfluenceConnector.render` uses this when choosing titles from record fields. It keeps title selection simple and predictable before the renderer builds the final recall text.

*Call graph*: called by 1 (render).


### Google workspace readers
Handles Google mailbox, calendar, document, drive, meeting, and spreadsheet content while sharing Google-specific error handling.

### `extensions/sources/ufo_ext_sources/providers/gmail.py`

`io_transport` · `source sync runs`

Gmail does not return an email as one simple block of text. A message is a nested MIME tree, which means the readable body may be split into plain-text and HTML parts, and those parts are stored in a special base64 encoding. This connector is the translator between Gmail’s API shape and the project’s source-sync shape.

On a first run, it lists message IDs from a pinned backfill window, usually recent mail, then chooses a Gmail history ID as the cursor for the next run. That cursor is like a bookmark in Gmail’s change log. On later runs, it asks Gmail what messages were added or deleted since that bookmark. New messages are fetched in batches, while deleted messages are returned as tombstones so the rest of the system can remove or mark them.

The file also turns each raw Gmail message into a flat record: sender, recipients, subject, labels, direction, and decoded body text. When the system later asks how to display the record, this connector renders it like a person would read an email: From, To, Cc, Subject, then the message body. If only HTML is available, it strips tags and keeps readable text.

#### Function details

##### `GmailConnector.paginate_source`  (lines 94–104)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the entry point the broader source-sync system uses to ask Gmail for pages of records. It passes along the saved cursor and the pinned backfill cutoff, then delegates the real Gmail-specific work to the connector’s pagination method.

**Data flow**: It receives an HTTP client, a stream description, an optional cursor, the current user ID, and an optional backfill date. It does not inspect the user ID here. It forwards the stream, cursor, and backfill date into Gmail pagination, and the output is an async sequence of pages containing message records, deletions, and the next cursor.

**Call relations**: The source framework calls this when it wants data from Gmail. This function immediately hands the work to GmailConnector.paginate so the rest of the file can decide whether to backfill old messages or walk Gmail’s change history.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 106–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main read loop for Gmail messages. It decides whether this run is an initial backfill or an incremental update, fetches message bodies for new messages, and packages everything into pages the sync system understands.

**Data flow**: It receives a stream, an HTTP client, an optional cursor, and an optional backfill cutoff. If there is no cursor, it gathers message IDs from the backfill window. If there is a cursor, it asks Gmail for added and deleted message IDs since that cursor. It fetches full bodies for added messages in chunks, then yields StreamPage objects with records, deletion IDs, and the next history cursor. If Gmail refuses because the account lacks the needed read permission, it turns that into a skipped stream instead of a failed run.

**Call relations**: GmailConnector.paginate_source calls this during source syncing. It calls _backfill for first-time reads, _history for later change-log reads, _fetch_bodies to turn IDs into full records, and google.refused_for_scope when an HTTP error may mean the Gmail grant lacks the required scope.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 2 external calls (__init__, refused_for_scope).


##### `GmailConnector._backfill`  (lines 145–170)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time scan of Gmail messages inside the configured backfill window. It also chooses a safe starting history cursor so future runs can switch from listing old mail to reading only changes.

**Data flow**: It receives an HTTP client and an optional cutoff date. It first reads the mailbox profile’s history ID as a safety floor, then lists message IDs from Gmail, adding an after:<timestamp> search filter when a cutoff exists. It follows Gmail page tokens until all matching IDs are collected. It returns the collected IDs and a seed history ID for the next run.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor. It uses _profile_history_id before listing messages, then _seed_history_id after listing, so even an empty backfill window can move the sync into incremental mode.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 172–175)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail’s current mailbox history ID from the profile endpoint. The connector uses it as a fallback bookmark when a backfill window contains no messages.

**Data flow**: It receives an HTTP client, asks Gmail for the mailbox profile, reads the historyId field, and returns it if it is a string. If Gmail does not provide a usable value, it returns None.

**Call relations**: GmailConnector._backfill calls this before listing messages. Reading it before the listing matters because it prevents a message delivered during an empty backfill scan from being accidentally skipped later.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 177–201)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the history cursor that future Gmail syncs should start from after a backfill. It prefers the newest listed message’s history ID, but falls back to the earlier profile history ID when needed.

**Data flow**: It receives an HTTP client, the list of message IDs found during backfill, and the profile history ID read before the listing. If there are message IDs, it fetches the first one in minimal form and uses its historyId if available. If that message disappeared or no message was listed, it returns the fallback floor value.

**Call relations**: GmailConnector._backfill calls this after collecting message IDs. Its result is passed back through paginate as the next cursor, which lets the next run use _history instead of repeating the same backfill forever.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 203–238)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log from a saved history ID. It finds which messages were added and which were deleted since the last successful sync.

**Data flow**: It receives an HTTP client and the previous history ID. It asks Gmail’s history endpoint for messageAdded and messageDeleted events, follows page tokens, and gathers message IDs into added and deleted sets. It removes IDs that were both added and deleted, then returns net-added IDs, deleted IDs, and the latest history ID. If Gmail says the old cursor expired, it raises CursorExpired so the core system can start over safely.

**Call relations**: GmailConnector.paginate calls this when a cursor exists. This function uses _message_ids to pull message IDs out of Gmail’s nested history records, then hands its added and deleted lists back so paginate can fetch new bodies and report tombstones.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 240–254)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns Gmail message IDs into full message records. It asks Gmail for each message body and skips messages that vanish between the history listing and the body fetch.

**Data flow**: It receives an HTTP client and a list of message IDs. For each ID, it requests the full Gmail message. A 404 response is treated as “already gone” and ignored; other errors still fail. Each fetched raw message is flattened into the project’s record shape, and the function returns the list of those records.

**Call relations**: GmailConnector.paginate calls this for each chunk of added IDs. It passes every raw Gmail response to _flatten_message, which extracts headers, addresses, labels, and decoded body text.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 256–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This creates the human-readable text used when a synced Gmail message is recalled or displayed. Instead of dumping Gmail’s nested JSON, it formats the message like an email with sender, recipients, subject, and body.

**Data flow**: It receives a flat message record and a stream description. For the messages stream, it reads the subject, sender, recipients, and body fields. It builds a title from the subject when possible, formats headers, chooses the best available body text, and returns both the title and the final prose. For non-message streams, it falls back to the parent connector’s renderer.

**Call relations**: The source framework calls this when it needs display text for a record. It relies on _str to safely read the subject, _format_contact and _format_recipients for email addresses, and _message_body to choose plain text, cleaned HTML, or snippet text.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 279–288)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This small helper extracts message IDs from Gmail history entries. Gmail wraps each ID inside nested objects, so this function pulls out only the useful strings.

**Data flow**: It receives an unknown value that should be a list of Gmail history entries. It ignores anything that is not shaped like a dictionary containing a message with a non-empty string ID. It returns a clean list of message ID strings.

**Call relations**: GmailConnector._history calls this while reading added and deleted events. It keeps the history-walking code focused on change logic instead of repeated nested-field checking.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 291–318)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Gmail message into the simple record shape the rest of the source system stores. It pulls important headers and decoded bodies out of Gmail’s nested payload.

**Data flow**: It receives the raw dictionary returned by Gmail’s messages.get endpoint. It reads selected headers such as From, To, Cc, and Subject, extracts plain-text and HTML bodies, parses addresses, copies labels, and marks the direction as outbound when the SENT label is present. It returns one flat dictionary with stable fields like id, thread_id, subject, sender, recipients, labels, and body text.

**Call relations**: GmailConnector._fetch_bodies calls this after each full message fetch. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 321–335)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail MIME payload for readable message bodies. It looks for the first plain-text body and the first HTML body, because either may be present depending on how the email was sent.

**Data flow**: It receives the nested payload dictionary from Gmail. It walks through the payload and all child parts, decodes body data for text/plain and text/html parts, and remembers the first body found for each type. It returns a pair: plain text if found, and HTML if found.

**Call relations**: _flatten_message calls this while building a flat message record. The nested helper _extract_bodies.walk does the recursive tree walk and calls _b64url_decode when it finds encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 325–332)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This inner helper walks through one MIME part and its children. It is like checking every folder in a filing cabinet until the first plain-text and HTML message bodies are found.

**Data flow**: It receives one payload part. It checks the part’s MIME type, reads body.data if present, decodes it when it is plain text or HTML, and stores it if that body type has not already been found. Then it repeats the same process for each child part.

**Call relations**: _extract_bodies uses this helper to inspect the whole nested Gmail payload. When encoded body content is found, it hands the string to _b64url_decode so the saved result is readable text.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 338–344)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s special body encoding into normal text. Gmail uses URL-safe base64, a text-safe encoding for binary data, and may omit padding characters, so this function fixes that before decoding.

**Data flow**: It receives an encoded string from Gmail. It adds any missing equals-sign padding, decodes the URL-safe base64 bytes, and converts them to UTF-8 text while replacing invalid characters. If the input cannot be decoded, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this whenever it finds a plain-text or HTML MIME body. Its output becomes the body_text or body_html field in the flattened message record.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 347–354)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This parses the first email address from a header such as From. It separates the email address from the optional display name.

**Data flow**: It receives a header string or None. If there is no header or no parsed address, it returns two None values. Otherwise it returns the first email address in lowercase and the display name if one was present.

**Call relations**: _flatten_message calls this for the From header. It uses Python’s email address parser so quoted names and common email header formats are interpreted correctly.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 357–364)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This parses a header containing one or more email addresses, such as To or Cc. It turns that header into a list of small recipient records.

**Data flow**: It receives a header string or None. If the header is missing, it returns an empty list. Otherwise it parses all addresses, lowercases each email address, keeps each display name when present, and returns a list of dictionaries with handle and display_name fields.

**Call relations**: _flatten_message calls this for recipient headers. Later, render uses those recipient records through _format_recipients to print readable To and Cc lines.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 367–372)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a readable email-address string. It prints either just the address or a display name followed by the address.

**Data flow**: It receives a possible email handle and display name. If the handle is not a non-empty string, it returns an empty string. If a display name is available, it returns “Name <address>”; otherwise it returns just the address.

**Call relations**: GmailConnector.render calls this for the sender, and _format_recipients calls it for each recipient. It gives all rendered email headers a consistent shape.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 375–382)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This turns a list of recipient records into one readable comma-separated line. It is used for To and Cc headers in the rendered email text.

**Data flow**: It receives a value that should be a list of recipient dictionaries. If the value is not a list, it returns an empty string. For each dictionary item, it formats the contact and joins all formatted contacts with commas.

**Call relations**: GmailConnector.render calls this when building To and Cc lines. It delegates the formatting of each individual person to _format_contact.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 385–393)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body for a message. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail’s short snippet if no full body is available.

**Data flow**: It receives a flat message record. It first checks body_text and returns it if it has real content. If not, it checks body_html and converts it to plain text. If neither exists, it returns the snippet if it is a string, otherwise an empty string.

**Call relations**: GmailConnector.render calls this when assembling the final display text. When HTML is the only full body available, it uses _HtmlText.extract to strip tags while preserving readable line breaks.

*Call graph*: called by 1 (render).


##### `_str`  (lines 396–397)

```
def _str(value: Any) -> str
```

**Purpose**: This safely treats a value as a string only when it really is one. It prevents non-string data from accidentally becoming a title or header value.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this for the subject. That keeps the rendering code simple and avoids showing unexpected values as email subjects.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 404–406)

```
def __init__(self) -> None
```

**Purpose**: This prepares an HTML-to-text parser for one email body. It sets up a place to collect readable text pieces as the parser reads the HTML.

**Data flow**: It receives no external data beyond the new parser instance. It initializes the base HTML parser with automatic character-reference conversion, then creates an empty list for collected text and line breaks. The result is a ready-to-use parser object.

**Call relations**: _HtmlText.extract creates this parser when an HTML body needs to be cleaned. The parser’s later callbacks fill the parts list as the HTML is read.


##### `_HtmlText.extract`  (lines 409–414)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It removes tags and attributes, keeps the words, and uses line breaks around block-like HTML elements.

**Data flow**: It receives raw HTML as a string. It feeds that HTML into a new parser, joins the collected text pieces, normalizes extra spaces on each line, removes blank lines, and returns the cleaned text.

**Call relations**: _message_body uses this when a message has HTML but no usable plain-text body. During parsing, the HTML parser invokes handle_data, handle_starttag, and handle_endtag to collect text and line breaks.


##### `_HtmlText.handle_data`  (lines 416–417)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records actual text found inside the HTML. It ignores the surrounding tags and keeps the words a reader would see.

**Data flow**: It receives a piece of text from the HTML parser. It appends that text to the parser’s internal parts list. It does not return a value; it changes the parser’s collected output.

**Call relations**: The HTML parser calls this while _HtmlText.extract is feeding it raw HTML. The collected pieces are later joined and cleaned into the final plain-text body.


##### `_HtmlText.handle_starttag`  (lines 419–421)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag usually marks a visual block, such as a paragraph or list item. That helps the plain-text output keep some of the email’s original structure.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline to the collected parts. Attributes are ignored because the goal is readable text, not preserving HTML behavior.

**Call relations**: The HTML parser calls this during _HtmlText.extract. It works with handle_endtag and handle_data so cleaned HTML does not become one long run-on line.


##### `_HtmlText.handle_endtag`  (lines 423–425)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a closing HTML tag ends a visual block. It helps separate paragraphs, table cells, headings, and similar sections in the cleaned text.

**Data flow**: It receives the tag name. If the tag is one of the known block tags, it appends a newline to the parser’s collected parts. It does not return anything; it only affects the final extracted text.

**Call relations**: The HTML parser calls this during _HtmlText.extract. Together with handle_starttag, it gives the final body readable spacing after HTML tags are removed.


### `extensions/sources/ufo_ext_sources/providers/google.py`

`domain_logic` · `sync error handling`

Google APIs can return the same broad refusal codes, such as 401 or 403, for very different reasons. One reason is permanent for the current account connection: the user did not grant the needed permission scope, so retrying will not help. Another reason is temporary: the connector used too much quota, like making too many requests in a time window, so waiting and retrying may work.

This file acts like a small interpreter for those Google error responses. It first looks inside the HTTP error response for Google’s structured error body. If the body is missing or is not valid JSON, it treats the details as empty rather than crashing. Then it checks whether the error details mention quota-related signals, such as Google’s RESOURCE_EXHAUSTED status or known usage-limit reason names.

The main decision is made by refused_for_scope. It says “yes, this was a real permission/scope refusal” only when the HTTP status is one of Google’s refusal statuses and the details do not point to quota. This prevents the sync driver from parking or skipping a stream just because Google temporarily throttled the connector. In everyday terms, it tells the system whether the door is locked because you lack the key, or whether the building is just temporarily full.

#### Function details

##### `error_detail`  (lines 30–37)

```
def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

*Call graph*: called by 1 (refused_for_scope); 1 external calls (dict_or_empty).


##### `is_quota_refusal`  (lines 40–44)

```
def is_quota_refusal(detail: dict[str, Any]) -> bool
```

*Call graph*: called by 1 (refused_for_scope); 1 external calls (list_or_empty).


##### `refused_for_scope`  (lines 47–52)

```
def refused_for_scope(error: httpx.HTTPStatusError) -> bool
```

*Call graph*: calls 2 internal fn (error_detail, is_quota_refusal).


### `extensions/sources/ufo_ext_sources/providers/googlecalendar.py`

`io_transport` · `source sync`

This connector is the bridge between Google Calendar and the rest of the sync system. Without it, calendar meetings would stay inside Google and would not become useful content for recall, search, or linking to people.

The file defines two streams. The first, `calendar_events`, stores one record per calendar event. The second, `event_attendees`, stores one record per invited person per event, like turning a guest list into individual rows. The connector reads Google’s events API in pages, because a calendar can have many events. On the first run it looks back 90 days and asks for deleted events too, so it can build a reliable starting snapshot. Later runs use Google’s `syncToken`, which is like a bookmark saying “continue from where I last left off.”

If Google says that bookmark has expired, the connector raises `CursorExpired` so the wider system knows to start fresh. If the user did not grant Calendar permission, it raises `StreamSkipped` rather than treating the whole run as broken.

The raw Google event shape is not stored directly. Helper functions flatten it into simpler fields: title, times, location, organizer, attendees, response status, and attendee role. The `render` method also turns an event into readable text, so a meeting can be recalled as a useful note rather than a pile of API fields.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 51–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads Google Calendar events page by page and turns them into sync pages the rest of the system can store. It supports both the event stream and the attendee stream, using Google’s sync cursor so later runs only fetch changes.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It builds the right Google API request: either a first-time lookback window or a continuation using the saved sync token. For each returned event, it either flattens the event into a normal event record, expands it into attendee rows, or records a deleted event id when an event was cancelled. It yields `StreamPage` objects containing new or changed records, deletions, and finally the next cursor to save for the next run.

**Call relations**: The sync runner calls this when it needs records from Google Calendar. Inside the loop it hands raw Google event objects to `_flatten_event` for the `calendar_events` stream or `_flatten_attendees` for the `event_attendees` stream. If Google reports an expired cursor, it raises `CursorExpired` so the core sync can restart cleanly; if Google refuses because the Calendar permission is missing, it uses `google.refused_for_scope` and raises `StreamSkipped` so the run records a skip instead of a failure.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 5 external calls (__init__, __init__, now, timedelta, refused_for_scope).


##### `GoogleCalendarConnector.render`  (lines 111–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a stored calendar event into a readable title and text body. This makes synced calendar data useful as human-readable recall content instead of just structured fields.

**Data flow**: It receives a record and the stream it came from. If the stream is not `calendar_events`, it falls back to the parent connector’s rendering behavior. For calendar events, it reads the title, start and end time, location, attendees, and description, then formats them into a short text note. It returns the chosen title and the formatted body.

**Call relations**: The wider system calls this when it needs a text version of a synced record. It uses `_str` to safely turn the title field into a string, then builds the readable event body itself. Non-calendar-event streams are handed back to the base `RestConnector` rendering path.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 139–166)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the simpler event record this system stores. It keeps the important human details, such as title, time, organizer, location, and invited people.

**Data flow**: It receives one event dictionary from Google. It reads nested fields such as start time, end time, organizer, and attendees, normalizes email addresses to lowercase, and converts Google’s time format into a single timestamp-style string. It returns one flat dictionary ready to be written as a `calendar_events` record.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for each non-cancelled event in the `calendar_events` stream. While building the record, it asks `_attendee` to simplify each invited person and `_parse_when` to normalize start and end times.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 169–175)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Simplifies one attendee from Google’s format into the small attendee summary stored inside an event record. It focuses on who the person is and how they responded.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee email into a stable handle, copies the display name, and maps Google’s response words, such as `needsAction`, into this system’s naming style, such as `needs_action`. It returns a compact attendee dictionary.

**Call relations**: `_flatten_event` calls this while folding an event’s attendee list into the main event record. It does not call other project functions; it is a small translator for attendee fields.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 178–204)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one calendar event into separate attendee records, one per invited person. This lets the system treat event attendance as its own relationship, not just text hidden inside the event.

**Data flow**: It receives one raw Google event. It reads the event id, organizer email, creation and update times, and the attendee list. For every attendee with a valid email, it creates a row with an id made from the event id and attendee handle, plus the attendee’s role, response, display name, and whether the attendee is the calendar owner. It returns a list of attendee-row dictionaries.

**Call relations**: `GoogleCalendarConnector.paginate` calls this when syncing the `event_attendees` stream. For each attendee row, it asks `_attendee_role` to decide whether the person is the organizer, a room or resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 207–214)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what kind of participant an attendee is for an event. It turns several Google flags into one simple role value.

**Data flow**: It receives an attendee dictionary and a separate yes-or-no value saying whether this attendee matches the organizer. It checks, in order, whether the attendee is the organizer, a resource such as a room, optional, or none of those. It returns one role string: `organizer`, `resource`, `optional`, or `required`.

**Call relations**: `_flatten_attendees` calls this while creating one row per attendee. The role it returns is copied directly into each attendee-stream record so downstream code can understand the invitee’s place in the meeting.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 217–226)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar time fields into one consistent string form. It supports both timed meetings and all-day events.

**Data flow**: It receives a value that should be Google’s start or end object. If the value contains `dateTime`, it returns that exact timestamp as a string. If it contains `date`, meaning an all-day event, it turns the date into a midnight UTC-style timestamp. If the input is missing or not shaped like a Google time object, it returns nothing.

**Call relations**: `_flatten_event` calls this for both the start and end fields of a calendar event. Its output becomes the `starts_at` and `ends_at` values stored on the flattened event record.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 229–230)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. This prevents non-text values from accidentally becoming titles in rendered calendar notes.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: `GoogleCalendarConnector.render` calls this when choosing the event title. It is a tiny guardrail that keeps the rendered text predictable even if a record is missing a proper title.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Google Docs. Its job is to find Google Docs through Google Drive, fetch each document through the Google Docs API, and package the result into records the rest of the sync system can store and render.

The flow has two stages. First, it asks Google Drive for files whose type is “Google Doc,” skipping trashed files and, when possible, asking only for files changed after the last saved sync point. This is like checking a library catalogue for books updated since your last visit. Drive returns file information such as the file id, title, link, owner, and modification time.

Second, for each file id, it asks the Google Docs API for the full document contents. If one listed document cannot be opened because access was removed or the file disappeared, the connector creates a small placeholder record instead of failing the whole sync. But if the whole Drive listing is refused because the Google permission grant is not enough, the stream is skipped with a clear reason.

Finally, `render` turns Google’s nested document structure into readable prose. Google stores text inside paragraph elements and text runs, so this file walks that tree and joins the visible text in order.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 51–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main sync loop for Google Docs. It collects document files from Drive, fetches each full document, combines Drive metadata with document content, and yields records in batches so the rest of the system can process them.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced modification time. It asks `_iter_doc_files` for Drive file pages, uses each file id to call `_document`, adds fields like title, URL, creation time, update time, and the original Drive file data, then outputs lists of document records. If Google refuses access for a permission-related reason, it turns that into a skipped stream instead of an unexplained crash.

**Call relations**: During a sync, the wider source framework calls this method to get Google Docs records. It relies on `_iter_doc_files` to discover candidate files and `_document` to fetch their contents. If Google returns an access error, it asks `ufo_ext_sources.providers.google.refused_for_scope` whether the problem is missing permissions, and then raises `StreamSkipped` so the runner can continue cleanly.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files); 1 external calls (refused_for_scope).


##### `GoogleDocsConnector._document`  (lines 89–98)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one Google Doc by its file id. It also protects the sync from failing just because one document is listed in Drive but cannot be opened through the Docs API.

**Data flow**: It receives an HTTP client and a Google file id. It requests the full document from the Google Docs API. If the request succeeds, the parsed document data comes back. If Google says the document is forbidden or not found, it returns a minimal record containing only the document id; other errors are passed upward.

**Call relations**: `paginate` calls this for every Google Docs file discovered in Drive. Its result is folded into the final record that `paginate` yields, so even unreadable or vanished documents can still leave a safe placeholder instead of stopping the whole batch.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 100–127)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through Google Drive’s file list and finds Google Docs that should be synced. It uses the saved cursor to avoid rereading older unchanged documents.

**Data flow**: It receives an HTTP client and an optional cursor time. It builds a Drive search query for non-trashed Google Docs, adds a “modified after this time” filter when a cursor exists, and requests Drive pages one by one. Each response’s `files` value is normalized into a list, yielded if non-empty, and the next page token is used until there are no more pages.

**Call relations**: `paginate` uses this as its source of Drive file metadata. This method calls `list_or_empty` so odd or missing `files` values become a safe empty list rather than surprising the caller.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 129–134)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a title and a readable text block. The rendered text is what a person would later see when the document is recalled or indexed.

**Data flow**: It receives a document record and the stream description. It pulls out a safe string title, asks `_plain_text` to extract the document body text, builds a simple heading that names the provider and stream, and returns both the title and the final formatted text.

**Call relations**: The source framework calls this after records have been fetched, when it needs text suitable for storage, search, or display. It delegates the tricky Google document tree traversal to `_plain_text`.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 137–153)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts the visible paragraph text from Google’s nested document format. It ignores document structures that do not contain ordinary paragraph text, such as section breaks or unsupported elements.

**Data flow**: It receives a document record. It looks inside `body.content`, walks each paragraph, then each paragraph element, and collects the `textRun.content` strings it finds. Those pieces are joined in order, trimmed at the ends, and returned as plain text.

**Call relations**: `GoogleDocsConnector.render` calls this whenever it needs the body text for a document. It is kept as a small helper so rendering stays simple while the Google-specific document structure is handled in one place.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledrive.py`

`io_transport` · `during Google Drive source sync runs`

This connector is the read-only bridge between UFO and Google Drive. Its job is to ask Google Drive for data in small pages, remember where it left off, and present the results in a consistent shape for the rest of the system.

The main stream is files. On the first run, it lists all non-trashed files, including files from shared drives, ordered by their last modified time. At the end of that first scan it asks Google for a “start page token,” which is like a bookmark for future changes. Later runs use that bookmark to read only what changed: new or updated files are returned as records, while removed or trashed files are returned as deletes. If Google says the bookmark is too old, the connector raises a special “cursor expired” signal so the larger system knows it must do a fresh scan.

Shared drives are simpler: the connector rereads the whole list each run. Permissions, comments, and revisions are child collections, so the connector first walks through files and then asks Google for each file’s related items.

A useful detail is how failures are treated. If the user’s authorization does not include Drive access, the stream is skipped rather than treated as a broken run. But other errors still surface. The file also provides a small renderer that turns file metadata into readable text with the file name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 87–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading Google Drive streams. Given a requested stream, it chooses the right paging routine and yields pages of records, deletes, or cursor updates back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. If the stream is files, it either reads incremental changes from the cursor or does a full file listing and then saves a new change bookmark. If the stream is shared drives, it lists drives. If the stream is permissions, comments, or revisions, it walks file-by-file and reads those child items. It yields record pages or StreamPage objects, and if Google refuses access because the grant lacks Drive scope, it turns that into a skip signal instead of a hard failure.

**Call relations**: The sync framework calls this when it wants data from one Google Drive stream. This method then hands the work to _paginate_file_changes, _paginate_files, _paginate_shared_drives, _paginate_file_children, or _start_page_token depending on the stream. If an access error looks like a missing Drive permission, it uses google.refused_for_scope and raises StreamSkipped so the larger run records the stream as skipped.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 2 external calls (__init__, refused_for_scope).


##### `GoogleDriveConnector._paginate_files`  (lines 121–146)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Google Drive files in pages. It is used for the initial full file scan and also as the starting point for reading child data such as comments or permissions.

**Data flow**: It receives an HTTP client and an optional modified-time cursor. It builds a Google Drive files query for non-trashed files, optionally only files modified after the cursor, then repeatedly asks the Drive API for up to 1000 files at a time. Each response’s files list is normalized into an empty list if missing, non-empty pages are yielded, and the next page token decides whether there is more to read.

**Call relations**: paginate calls this directly when the files stream needs a first full listing. _paginate_file_children also calls it so it can discover each file before asking for that file’s permissions, comments, or revisions.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 148–153)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the bookmark used to read future file changes. Without this token, later syncs would not know where the previous file scan ended.

**Data flow**: It receives an HTTP client, calls Google’s startPageToken endpoint, and reads the startPageToken value from the response. If the token is a non-empty string, it returns it; otherwise it returns nothing.

**Call relations**: paginate uses this after a first full files scan. The returned token is wrapped in a StreamPage as the next cursor, giving the broader sync system a place to resume from on the next run.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 155–200)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads changes to Google Drive files since a saved bookmark. It separates live file updates from deletions so the rest of the system can update stored records or remove stale ones.

**Data flow**: It receives an HTTP client and a required cursor token. It repeatedly calls Google Drive’s changes endpoint, asking for changed and removed files. For each change, it ignores malformed entries, adds removed or trashed file IDs to a delete list, and adds valid file objects to a records list. It yields StreamPage objects containing records, deletes, and the next cursor. If Google returns status 410, meaning the token has expired, it raises CursorExpired.

**Call relations**: paginate calls this whenever the files stream already has a saved cursor. It creates StreamPage results for the sync framework and uses list_or_empty to safely deal with missing or oddly shaped changes lists.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 202–219)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists the shared drives available to the authorized Google account. Shared drives are reread as a whole rather than tracked through the changes bookmark.

**Data flow**: It receives an HTTP client and starts with no page token. It asks Google Drive for shared drives in pages, yields any non-empty drive lists, then follows Google’s nextPageToken until there are no more pages.

**Call relations**: paginate calls this when the requested stream is shared_drives. Like the other pagers, it uses list_or_empty so a missing drives field behaves like an empty page instead of crashing.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 221–259)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads per-file child data: permissions, comments, or revisions. It works by visiting every file first, then asking Google for that file’s related collection.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. First it gets all current files through _paginate_files. For each file with a valid ID, it calls the matching child endpoint, follows child page tokens, and collects the returned child records. If the child stream has a cursor field, it filters out records that are not newer than the cursor. Each yielded child record is enriched with the parent file’s ID and name. If Google refuses a particular child request with 403 or 404, it quietly skips that file’s child collection and moves on.

**Call relations**: paginate calls this for permissions, comments, and revisions. This function relies on _paginate_files to provide the list of parent files, then uses list_or_empty to safely read the child collection from each Google response.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 261–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a small readable text summary. That summary can be stored or shown as recallable metadata instead of leaving the record as raw API data.

**Data flow**: It receives one record and its stream description. For non-file streams, it delegates to the base connector’s rendering behavior. For file records, it extracts a safe title, MIME type, owners, and web link, then returns the title plus a short multi-line text body.

**Call relations**: The broader source system calls this when it needs a human-readable representation of a synced record. For file records it uses the local _str helper to avoid treating non-text names as valid titles; for other streams it hands rendering back to the parent RestConnector.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 279–280)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into text only if it is already a string. It prevents unexpected non-string values from becoming misleading titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when reading a file name. It keeps the renderer simple and defensive when Google’s response is missing a name or contains an unexpected type.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googlemeet.py`

`io_transport` · `during source sync and page rendering`

This connector is like a careful librarian for Google Meet. It visits Google’s Meet API, finds recent conference records, checks whether each meeting has a transcript or AI notes, and then builds one readable page per useful meeting. It does not emit empty meetings, but it still advances its time marker so future syncs do not keep rechecking the same old conference window forever.

The main flow starts with conference records, which are the meeting-level containers. For each conference, the connector asks for transcript sessions and smart-note sessions. Transcript sessions may contain many per-speaker entries, so the connector fetches those too and later formats them as dialogue. Smart notes often point to a Google Docs document; when possible, the connector also reads that document and extracts its plain text. If the document is missing or access is denied, the connector keeps the link instead of failing the whole sync.

The file also deals with incremental syncing. It uses a one-day lookback from the last saved start time because Google may generate transcripts or notes after a meeting ends. If Google refuses access because the connected account lacks the right permission, the stream is marked as skipped rather than treated as a system failure.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 55–89)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main stream reader for Google Meet meeting artifacts. It walks through conference records page by page, fetches the useful details for each meeting, and yields batches of records for the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor time. It builds a Google Meet query, applying a short lookback if a cursor exists, then reads conference pages. For each conference, it builds a full meeting record and keeps only meetings that have transcripts or smart notes. It outputs StreamPage objects containing those records and the next cursor time; if access is refused because of permissions, it changes the outcome into a skipped stream.

**Call relations**: The source sync machinery calls this when it needs Google Meet data. It uses _lookback to avoid missing late-generated artifacts, _max_start_time to move the cursor forward, and _conference_record to expand each conference into a complete record. If Google reports a permission-style refusal, it asks google.refused_for_scope and raises StreamSkipped so the run can continue without treating the stream as broken.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 3 external calls (__init__, list_or_empty, refused_for_scope).


##### `GoogleMeetConnector._conference_record`  (lines 91–114)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete internal record for one Google Meet conference. It gathers the meeting’s transcripts and smart notes and adds basic meeting metadata such as start and end time.

**Data flow**: It takes one raw conference dictionary from Google. It reads the conference name, fetches transcript artifacts and smart-note artifacts under that conference, converts each artifact into a cleaner record, and returns one combined dictionary for the meeting. The returned record includes a stable id, title, original conference name, timing fields, transcripts, and smart notes.

**Call relations**: paginate calls this for every conference it sees. This function fans out to _artifacts to list child objects, then sends transcript items to _transcript and note items to _smart_note. It uses _resource_id and _str to turn Google’s resource names into safe text fields.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 116–133)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This lists child artifacts, such as transcripts or smart notes, under a specific conference. It hides the details of Google’s paged API responses from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference name, and the artifact collection name to read. If there is no parent name, it returns an empty list. Otherwise, it repeatedly asks Google for pages of that artifact collection, appends the returned items, follows any next-page token, and finally returns one list of artifacts.

**Call relations**: _conference_record calls this twice: once for transcripts and once for smart notes. The function uses list_or_empty so missing or oddly shaped response fields become an empty list instead of crashing the normal flow.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 135–147)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google into the connector’s simpler transcript record. It also pulls in the individual spoken entries for that transcript.

**Data flow**: It takes a raw transcript dictionary. It extracts the transcript name, state, time range, and Google Docs destination, then calls _transcript_entries to fetch the per-speaker lines. It returns a dictionary containing the transcript id, metadata, document link fields, and entries.

**Call relations**: _conference_record calls this for each transcript artifact returned by _artifacts. This function uses _docs_destination for the linked Google Docs information, _resource_id for a short id, _str for safe string extraction, and _transcript_entries for the detailed dialogue.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 149–182)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This reads the individual lines inside a transcript, including who spoke, what they said, and when. It is what turns a transcript artifact from a summary object into useful conversation text.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty, it returns an empty list. Otherwise, it reads transcript-entry pages from Google, converts each entry into a smaller dictionary with id, participant, text, language, and timing fields, and returns the collected list. If Google says the document or entry source is missing or forbidden with certain statuses, it returns whatever entries it already has instead of failing.

**Call relations**: _transcript calls this while building a transcript record. It uses list_or_empty to safely read entry lists and _resource_id and _str to normalize resource names. If an unexpected HTTP error happens, it lets that error rise to the caller.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 184–199)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a clean record, and tries to include the actual note text when Google Docs allows it. Smart notes are the AI-generated meeting summaries.

**Data flow**: It receives a raw smart-note dictionary. It extracts the note id, name, state, time range, and Google Docs destination. If there is a document id, it calls _document_text to fetch and flatten the Google Doc; when text is found, it adds that text as the body. It returns the enriched note record.

**Call relations**: _conference_record calls this for each smart-note artifact returned by _artifacts. It uses _docs_destination to find the linked document, _document_text to read its contents, and _resource_id and _str to make identifiers and strings safe.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 201–210)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a Google Docs document and extracts the plain text from it. It is used so AI notes can be searchable as text, not just stored as a link.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely quotes the id for use in a URL, requests the document, and passes the returned document structure to _plain_text. It returns the extracted text. If Google says the document is missing or access is forbidden, it returns an empty string instead of stopping the sync.

**Call relations**: _smart_note calls this when a smart note points to a Google Docs document. This function delegates the document-shape parsing to _plain_text and uses URL quoting so document ids are safe inside the request path.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 212–229)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Google Meet meeting record into the final human-readable page text. That page is what a person, search index, or recall system can read later.

**Data flow**: It receives a meeting record and stream description. For the Google Meet meeting_artifacts stream, it builds a title, a labeled block of meeting metadata, a transcript section, and an AI-summary section, then joins the non-empty pieces into one text document. It returns the page title and page body. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The broader source framework uses this after records have been fetched. It calls _labeled for simple metadata, _transcripts_section for transcript content, _smart_notes_section for AI notes, and _str to safely read text fields.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 232–248)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript records for a meeting into a readable “Transcripts” section. It includes transcript metadata and turns entries into a dialogue-style block.

**Data flow**: It receives a value that should contain transcript records. It treats missing or invalid values as an empty list. For each transcript, it builds labeled metadata such as state, start, end, and document URL, then adds dialogue text from the entries. It returns one formatted string, or an empty string if there are no transcripts.

**Call relations**: render calls this while assembling the final meeting page. This function relies on _dialogue to format the spoken entries, _labeled for metadata lines, _str for safe string extraction, and list_or_empty for tolerant list handling.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 251–267)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats all AI-generated meeting notes into a readable “AI summaries” section. It shows note metadata and includes the note body when it was successfully read from Google Docs.

**Data flow**: It receives a value that should contain smart-note records. It converts missing or invalid data into an empty list. For each note, it creates labeled metadata such as state, times, and document URL, then appends the body text if present. It returns a formatted section string, or an empty string when there are no notes.

**Call relations**: render calls this as one part of the final page body. It uses _labeled to format metadata, _str to safely read body and fields, and list_or_empty to avoid breaking on missing note lists.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 270–283)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns transcript entries into readable conversation lines. It also combines consecutive lines from the same speaker so the transcript feels less choppy.

**Data flow**: It receives a value that should contain transcript entries. It loops through the entries, skips blank text, turns the participant field into a speaker name, and writes lines like “Speaker: text”. If the same speaker continues on the next entry, it appends the text to the previous line. It returns the joined dialogue text.

**Call relations**: _transcripts_section calls this when it needs to show transcript entries. This function uses _speaker to name participants, _str to safely read text, and list_or_empty to treat missing entries as no dialogue.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 286–299)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. It strips away document formatting and keeps only the written content.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the body content, walks through paragraph elements, finds text runs, collects their text content, joins everything together, trims outer whitespace, and returns the result. If the expected structure is missing, it simply returns an empty string.

**Call relations**: _document_text calls this after successfully fetching a Google Docs document. It is the small parser that turns Google’s structured document response into plain text suitable for the final meeting page.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 302–309)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls Google Docs link information out of a Meet artifact. It gives the rest of the connector a consistent way to find the document id and export URL.

**Data flow**: It receives a raw transcript or smart-note dictionary. It checks for a docsDestination object. If present, it extracts the document id and export URL as strings and returns them under the keys docs_document and docs_url. If there is no usable destination, it returns an empty dictionary.

**Call relations**: _transcript and _smart_note both call this when building artifact records. It uses _str so non-string document fields become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 312–318)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest meeting start time from a page of conference records. It helps the connector remember how far it has already scanned.

**Data flow**: It receives a list of conference dictionaries and the current cursor value. It compares each conference’s startTime string with the current cursor and keeps the greatest one. It returns the updated cursor, or the original cursor if no later start time is found.

**Call relations**: paginate calls this after reading each page of conferences. The returned value becomes the next cursor in the StreamPage, so future syncs can start near the newest known conference instead of rescanning everything.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 321–323)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a saved cursor back by one day. It exists because Google may create transcripts or AI notes after the meeting has already ended.

**Data flow**: It receives an ISO-formatted timestamp string. It parses that time, subtracts the configured one-day lookback, formats the result back as a timestamp with milliseconds, and returns it. The output is used in the Google Meet filter.

**Call relations**: paginate calls this when it has a previous cursor. The adjusted time lets paginate refetch a small recent window, which helps catch late-arriving artifacts without scanning the whole history.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 326–327)

```
def _resource_id(name: str) -> str
```

**Purpose**: This takes a full Google resource name and returns only the final id part. For example, it turns a slash-separated path into the short piece after the last slash.

**Data flow**: It receives a string resource name. If the string is not empty, it splits from the right on “/” and returns the last piece; if the input is empty, it returns an empty string. It does not change anything outside itself.

**Call relations**: _conference_record, _transcript, _transcript_entries, _smart_note, and _speaker use this whenever they need a compact id from a Google resource path. It keeps id creation consistent across meetings, transcripts, entries, notes, and participants.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 330–332)

```
def _speaker(value: Any) -> str
```

**Purpose**: This turns a participant value from a transcript entry into a display name for dialogue. If it cannot find a useful name, it uses the friendly fallback “Participant”.

**Data flow**: It receives any participant value. It first keeps the value only if it is a string, then extracts the final resource id. It returns that id if present, otherwise it returns “Participant”.

**Call relations**: _dialogue calls this for each transcript entry that has text. _speaker uses _str and _resource_id so dialogue formatting can rely on a simple speaker label.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 335–336)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only when it is already a string. It prevents unexpected numbers, objects, or missing fields from leaking into formatted output.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise, it returns an empty string. It has no side effects.

**Call relations**: Many functions call this while reading Google response fields or building page text, including _conference_record, _transcript, _transcript_entries, _smart_note, render, _dialogue, _docs_destination, and the section formatters. It acts like a simple guardrail around data from outside the program.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 339–340)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats simple metadata as “label: value” lines. It keeps only fields that actually have a value, so the final page does not fill up with empty labels.

**Data flow**: It receives a list of label-and-value pairs. It filters out pairs whose value is empty, formats the remaining pairs as one line each, joins them with newlines, and returns the resulting text.

**Call relations**: render uses this for top-level meeting metadata, while _transcripts_section and _smart_notes_section use it for artifact metadata. It gives all these sections the same simple label style.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/providers/googlesheets.py`

`io_transport` · `during source sync`

This connector solves a practical problem: Google Sheets data is split across two Google services. Drive knows which spreadsheet files exist and when they changed. Sheets knows the tabs and cell values inside each spreadsheet. This file combines both views into three streams: spreadsheet metadata, sheet-tab metadata, and sheet cell values.

A sync run starts by asking Drive for spreadsheet files, ordered by their modified time. It uses a cursor, like a bookmark, so later runs only revisit files changed at or after the last saved time. The connector then fans out: for each Drive file, it asks the Sheets API for spreadsheet details, then turns those details into the right records for the requested stream.

The file is careful about partial access. If the user cannot read one spreadsheet or one tab, that file or tab is marked as refused without blocking the whole sync. The cursor can carry refused file IDs so a later run can retry them if permissions improve. But if the whole grant is missing a needed Google permission, the stream is skipped instead of pretending only one file failed.

For tab values, it reads sheets in batches to avoid too many requests, and falls back to one-tab-at-a-time reads when a batch is refused. It also renders records into simple text, so spreadsheet titles, tab names, and grid rows become human-readable content.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 152–202)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main paging loop for a Google Sheets sync stream. It reads spreadsheet files, turns them into records for the chosen stream, tracks progress with a cursor, and remembers files that could not be read so they can be retried later.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. It decodes the cursor into a watermark time, refused file IDs, and retry state; walks changed spreadsheets; collects records into pages; updates the watermark from Drive modified times; and yields StreamPage objects with records and the next cursor. If Google says the whole grant cannot read Drive or Sheets, it turns that into a stream skip instead of a normal failure.

**Call relations**: The sync framework calls this when it wants records. It calls _decode_cursor first, then uses _spreadsheet_visits for the normal Drive listing, _visit_records to create stream-specific records, _settled to update the refused-file set, _carried_visit to retry previously refused files, and _encode_cursor whenever it needs to report progress.

*Call graph*: calls 7 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _settled); 2 external calls (__init__, refused_for_scope).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 204–229)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists spreadsheet files from Google Drive in chunks. It is the part that asks Drive, 'Which Google Sheets files should I consider for this sync?'

**Data flow**: It receives an HTTP client and an optional watermark time. It builds a Drive search query for non-trashed spreadsheet files, adds a modified-time filter if there is a watermark, follows Drive page tokens, and yields each non-empty batch of file metadata. It reads Drive's nextPageToken to know when there are no more pages.

**Call relations**: _spreadsheet_visits calls this to get the raw Drive file batches. This function does not build final records itself; it only supplies file metadata that later steps enrich with Sheets API data.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 231–239)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This function turns Drive file listings into spreadsheet visits. A visit is the connector's internal note saying, 'I looked at this file, here is its record if I could read it, and here is whether access was refused.'

**Data flow**: It receives an HTTP client and a watermark. It reads batches from _iter_spreadsheet_files, extracts each valid spreadsheet ID, and asks _file_visit to fetch or synthesize the spreadsheet record. It yields one _FileVisit per usable file ID.

**Call relations**: paginate calls this during the normal listing phase. For each file it finds, it hands control to _file_visit, which combines Drive metadata with Sheets metadata or records a per-file refusal.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 241–255)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This retries a spreadsheet ID that was refused in an earlier run. It lets the connector pick up a file later if the user's Google permissions changed.

**Data flow**: It receives an HTTP client and a file ID from the saved cursor. It asks Drive for that one file, including whether it was trashed. If the file is gone or trashed, it returns a visit with no record and no retry needed; if access is still refused, it returns a refused visit; otherwise it passes the file metadata to _file_visit and returns the resulting visit.

**Call relations**: paginate calls this after finishing the normal Drive listing, but only for carried refused IDs that were not already seen in the listing. It relies on _is_per_file_refusal and google.error_detail to decide whether an error really belongs to this one file.

*Call graph*: calls 2 internal fn (_file_visit, _is_per_file_refusal); called by 1 (paginate); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._file_visit`  (lines 257–284)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This builds the connector's main spreadsheet record for one Drive file. It combines Drive metadata, such as modified time and web link, with Sheets metadata, such as spreadsheet title and tab list.

**Data flow**: It receives an HTTP client, a file ID, and Drive's metadata for that file. It asks the Sheets API for spreadsheet details. If that one file is refused, it keeps going with a minimal record based on Drive metadata; otherwise it merges the Sheets response with Drive fields into a normalized record and marks whether the file was refused.

**Call relations**: _spreadsheet_visits uses this for files found through Drive, and _carried_visit uses it for retried refused files. It calls _is_per_file_refusal to distinguish a single unreadable file from broader problems that should be raised.

*Call graph*: calls 1 internal fn (_is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._visit_records`  (lines 286–302)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This chooses how to turn one spreadsheet visit into records for the requested stream. It is the small dispatcher that says whether the caller wants spreadsheets, tabs, or tab values.

**Data flow**: It receives an HTTP client, a stream description, and a _FileVisit. If the visit has no record, it returns no records but preserves the refusal flag. For the spreadsheet stream it returns the spreadsheet record; for the sheets stream it expands tabs with _sheet_records; for the sheet_values stream it reads grid values through _sheet_value_records.

**Call relations**: paginate calls this for every visited spreadsheet. It hands tab metadata work to _sheet_records and cell-value work to _sheet_value_records, then passes the records and refusal status back to paginate for paging and cursor updates.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 304–360)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This reads the actual cell rows from each tab in a spreadsheet. It produces one record per tab containing the rows that Google Sheets returned.

**Data flow**: It receives an HTTP client and a spreadsheet record. It extracts the tab titles and IDs, groups them into bounded batches, asks Sheets values:batchGet for their rows, checks that Google returned one answer per requested tab, and creates value records. If a batch is refused for a per-file or per-tab reason, it retries each tab individually and skips only the tabs that remain refused.

**Call relations**: _visit_records calls this when the active stream is sheet_values. It uses _quoted_sheet_range to name tabs safely, _sheet_value_record to build each output record, _is_per_file_refusal to classify access errors, and raises StreamFault if the Sheets API returns a structurally surprising batch response.

*Call graph*: calls 4 internal fn (__init__, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 3 external calls (list_or_empty, error_detail, quote).


##### `GoogleSheetsConnector.render`  (lines 362–381)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Sheets record into readable text. The rest of the system can use that text as recallable or searchable content.

**Data flow**: It receives one record and the stream it came from. For spreadsheet records, it makes a title plus a list of tab names; for sheet records, it names the parent spreadsheet; for value records, it converts rows into plain text lines. It returns a display title and a formatted body.

**Call relations**: The source framework calls this when it needs a human-readable version of a record. It uses _str to avoid non-text titles leaking into headings and _grid_text to turn cell rows into simple text.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 384–397)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This reads the saved sync cursor, which is the connector's bookmark. It supports both old simple timestamp cursors and newer JSON cursors that also remember refused file IDs.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns empty starting state. If the cursor is plain text or not valid JSON, it treats it as just a watermark time. If it is valid checkpoint JSON, it validates it and returns the watermark, refused IDs, and retry marker.

**Call relations**: paginate calls this at the start of a sync page sequence. Its output drives which Drive files are listed and which previously refused files are retried.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 400–405)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This writes the connector's bookmark after progress has been made. It keeps the cursor simple when possible, but stores extra retry information when there are refused files.

**Data flow**: It receives a watermark time, a set of refused file IDs, and an optional retried ID. If there is no watermark or no refused file, it returns just the watermark. Otherwise it builds a JSON checkpoint with the watermark, a limited sorted list of refused IDs, and the retry marker when present.

**Call relations**: paginate calls this whenever it yields a page or needs to report final progress. The cursor it creates is later read by _decode_cursor on the next run.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 408–409)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This updates the remembered set of refused files after a file has just been tried. It answers the simple question: should this file still be retried later?

**Data flow**: It receives the current refused-file set, one file ID, and whether that file is still refused. If it is still refused, the ID is added; if it is no longer refused, the ID is removed. It returns the updated set.

**Call relations**: paginate calls this after _visit_records finishes for both listed files and carried retry files. The result feeds into _encode_cursor so future runs know what remains unresolved.

*Call graph*: called by 1 (paginate).


##### `_is_per_file_refusal`  (lines 412–418)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This decides whether a Google API error means 'this one file or tab cannot be read' rather than 'the whole connector is not allowed' or 'the run hit quota.' This distinction prevents one private spreadsheet from stopping the entire sync.

**Data flow**: It receives an HTTP status code and parsed Google error details. It returns true only for 403 or 404 errors with a real Google error body, not quota-related, and not a grant-wide permission problem. Otherwise it returns false.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this when Google returns an error. It calls _is_grant_refusal and google.is_quota_refusal to separate local file refusals from broader failures.

*Call graph*: calls 1 internal fn (_is_grant_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records); 1 external calls (is_quota_refusal).


##### `_is_grant_refusal`  (lines 421–426)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This checks whether a Google error says the user's connection or the Google project lacks the required Drive or Sheets permission. That kind of problem affects the whole stream, not just one spreadsheet.

**Data flow**: It receives parsed Google error details. It looks through Google's error lists for known permission reasons or service-domain details, and returns true if it finds them. Otherwise it returns false.

**Call relations**: _is_per_file_refusal calls this while classifying errors. If this function says the refusal is grant-wide, callers do not use the per-file fallback path.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 429–450)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This expands one spreadsheet record into one record per sheet tab. It gives each tab its own identity so tabs can be synced and recalled separately from the whole spreadsheet.

**Data flow**: It receives a spreadsheet record that includes a Sheets API tab list. It skips malformed tab entries, copies useful tab data, adds a stable ID made from spreadsheet ID and sheet ID, and stamps the parent spreadsheet title and timestamps onto each tab record. It returns the list of tab records.

**Call relations**: _visit_records calls this when the active stream is sheets. Its output goes back to paginate as the page's records.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 453–466)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the record for one tab's cell values. It wraps Google's value-range response with parent spreadsheet and tab information.

**Data flow**: It receives the parent spreadsheet record, the tab title, the tab ID, and Google's value-range data. It returns a dictionary with the value data plus a stable ID, spreadsheet ID and title, sheet ID and title, and the parent timestamps.

**Call relations**: _sheet_value_records calls this after each successful batch or individual tab read. The resulting records are returned upward through _visit_records to paginate.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 469–471)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This safely formats a Google Sheets tab title as an A1-style sheet reference. Quoting matters because an unquoted tab name can be mistaken for a cell reference or named range.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title, then wraps the whole title in single quotes. The returned string is safe to send as a Sheets API range for the entire tab.

**Call relations**: _sheet_value_records calls this when preparing batch and individual value requests. Its output is sometimes URL-encoded before being placed into a request path.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 474–479)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This turns rows of cell values into plain text. It makes a spreadsheet grid readable without preserving full spreadsheet formatting.

**Data flow**: It receives an unknown value. If the value is not a list of rows, it returns an empty string. For each row that is a list, it converts cells to text, joins cells with ' | ', and joins rows with newlines.

**Call relations**: render calls this for sheet_values records. The text it returns becomes the body used for human-readable recall.

*Call graph*: called by 1 (render).


##### `_str`  (lines 482–483)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely accepts text fields only when they are really strings. It avoids showing Python-style non-text values in rendered headings and bodies.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render calls this when building titles and short descriptions for spreadsheet and sheet records.

*Call graph*: called by 1 (render).


### Microsoft Teams collaboration
Reads Microsoft Teams teams, channels, chats, and messages through Microsoft Graph into searchable records.

### `extensions/sources/ufo_ext_sources/providers/microsoft_teams.py`

`io_transport` · `source sync`

This connector is the bridge between the project and Microsoft Teams. Without it, the system would not know where to ask Microsoft for a user’s joined teams, channels, chats, or messages, nor how to turn those replies into readable content.

Microsoft Graph returns data in pages, a bit like a long catalog that says “see the next page here.” This file follows those pages for each kind of Teams data. It first asks for the teams the signed-in user has joined. For each team, it asks for that team’s channels. For each channel, it asks for messages. It also asks for the user’s chats, then messages inside each chat.

Messages are synced incrementally. That means the connector can receive a saved “watermark” time and only pass along messages changed after that time, instead of rereading everything every run. When Microsoft says one particular team, channel, or chat is forbidden or missing, the connector skips that parent and keeps going, so one bad room does not ruin the whole sync. But if Microsoft refuses the top-level permission check, the stream is marked as skipped rather than treated as a crash.

For display, normal structural records use the default JSON-style rendering. Message records get special treatment: their HTML body is stripped down to plain text and placed under a simple heading.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the full list of Microsoft Teams that the signed-in user has joined. Other parts of the connector use this as the starting point before looking for channels and messages.

**Data flow**: It receives an HTTP client that already knows how to talk to Microsoft Graph. It requests the user’s joined teams, follows all result pages, gathers every team record into one list, and returns that list.

**Call relations**: This is the first step for team-based data. The channel reader calls it so it knows which teams to inspect, and the main pagination entry point calls it directly when the requested stream is teams.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside each joined team. It also attaches the parent team’s identity to every channel record, so later steps know where each channel came from.

**Data flow**: It starts by asking _teams for the user’s teams. For each team with a valid ID, it requests that team’s channels from Microsoft Graph, adds context such as team_id and team_name to each channel record, and yields channel pages onward. If one team’s channels are forbidden or missing, it skips that team and continues with the rest.

**Call relations**: This function sits between teams and channel messages. The channel-message reader calls it to discover channels, and paginate calls it when the sync asks for the channels stream. It uses with_context to label channel records with their parent team details.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from every channel the user can access. It supports incremental syncing by only yielding messages newer than the saved cursor when one is provided.

**Data flow**: It receives an HTTP client and an optional cursor value, which is the last synced modification time. It asks _channels for channels, uses each channel’s team and channel IDs to request messages, filters out older messages when a cursor exists, adds context such as team_id, channel_id, and thread_id, then yields only non-empty message batches. If one channel cannot be read because it is forbidden or missing, it skips that channel and keeps going.

**Call relations**: This is called by paginate when the requested stream is channel_messages. It depends on _channels to know where to look, and it hands enriched message records onward using with_context so downstream indexing can relate each message back to its channel thread.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of one-to-one or group chats visible to the signed-in user. This is the starting point for syncing chat messages.

**Data flow**: It receives an HTTP client, requests the user’s chats from Microsoft Graph, follows every page of results, collects all chat records into a list, and returns that list.

**Call relations**: The chat-message reader calls it before asking for messages inside each chat. The paginate method also calls it directly when the requested stream is chats.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from each Microsoft Teams chat the user can access. Like channel message syncing, it can skip messages that have not changed since the last sync.

**Data flow**: It receives an HTTP client and an optional cursor time. It asks _chats for available chats, requests messages for each valid chat ID, filters by lastModifiedDateTime when a cursor is present, adds chat_id and thread_id context, and yields message batches that still contain records. If a specific chat is forbidden or missing, it skips that chat instead of stopping the whole run.

**Call relations**: Paginate calls this when syncing the chat_messages stream. It depends on _chats to discover chat IDs, then uses with_context to label each message so later stages know which chat thread it belongs to.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the connector’s dispatcher for reading each supported Teams stream. Given a stream name, it chooses the right helper to fetch teams, channels, chats, or messages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields pages of records. If Microsoft refuses access at the top level with an authorization-style error, it turns that into a StreamSkipped result so the sync records a clean skip. If the stream name is unknown, it also reports it as skipped rather than pretending it worked.

**Call relations**: This is the main method the broader source-sync framework calls to pull data from this connector. It hands work to _teams, _channels, _channel_messages, _chats, or _chat_messages depending on what the framework is currently syncing.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into a title and readable text for indexing or recall. It gives message records a nicer plain-text body instead of leaving Microsoft’s HTML untouched.

**Data flow**: It receives one record and the stream it came from. For non-message streams, it delegates to the shared default renderer. For channel and chat messages, it reads the subject, pulls body.content from the nested record, strips HTML tags from that body, builds a Markdown-like heading, and returns the title plus cleaned text.

**Call relations**: The sync framework uses this after records are fetched to create human-readable pages. For Teams messages it calls _str, _strip_html, and get_path; for other record types it relies on the base RestConnector behavior.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a small piece of HTML text into plain text by removing tags. This matters because Microsoft Graph stores Teams message bodies as HTML, but the project wants readable text.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces HTML tags with spaces, trims extra space from the ends, and returns the cleaned text.

**Call relations**: MicrosoftTeamsConnector.render calls this when preparing channel and chat messages. It is a small helper used only for making message bodies easier to read.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only when it is already a string. It prevents non-text values from accidentally becoming confusing titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: MicrosoftTeamsConnector.render uses this to read a message subject safely before building the page title and heading.

*Call graph*: called by 1 (render).


### Notion workspace pages
Reads Notion users, pages, data sources, comments, and nested blocks into human-readable searchable text.

### `extensions/sources/ufo_ext_sources/providers/notion.py`

`io_transport` · `source sync and record rendering`

Notion stores useful text in many different shapes: page titles live in properties, page body text lives in blocks, comments have rich-text runs, and users have names and emails. If the system simply saved Notion’s raw JSON, much of that content would be hard for a person or recall system to understand. This connector is the translator between Notion’s API and the project’s source-sync system.

The file defines which Notion streams can be read, such as pages, blocks, comments, and users. During a sync, it builds an HTTP client with Notion’s required version header, asks Notion for records page by page, and skips streams gracefully when the Notion integration is not allowed to see them. That matters because a missing Notion permission should not necessarily fail the whole sync.

Pages and data sources are found through Notion search. Blocks are gathered by walking down each page’s block tree, like opening folders inside folders, but with a safety depth limit and with special block types left alone because they are separate records. Comments are fetched per page. Finally, `render` converts each raw record into prose: a title plus readable body text. This is what makes synced Notion content useful later, because it resembles what a Notion member would actually read.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Notion and adds the required Notion API version header. Without this header, Notion may reject requests or interpret them using the wrong API version.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to build the normal authenticated client, then adds `Notion-Version` to that client’s headers. It returns the ready-to-use client.

**Call relations**: This is part of the setup path inherited from the REST connector. Later sync calls use the client it creates when `paginate` and the helper methods make Notion API requests.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Notion stream. Given a stream such as pages, users, comments, or blocks, it chooses the right fetching method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the last synced time. It checks the stream name, calls the matching helper, and yields each list of records that helper produces. If Notion replies with a permission-related error, it turns that into a clean stream skip instead of crashing the whole run.

**Call relations**: The sync engine calls this when it needs records for a Notion stream. It hands work to `_collection` for users, `_search` for pages and data sources, `_comments` for comments, and `_blocks` for page body blocks. If the stream is unknown or Notion refuses access, it raises `StreamSkipped` so the larger run can record that outcome.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Notion for pages or data sources and returns them in batches. It also applies the project’s incremental-sync cutoff, meaning it keeps only records edited after the saved cursor.

**Data flow**: It receives the HTTP client, the kind of Notion object to search for, and an optional last-edited cursor. It sends repeated `POST /search` requests sorted by last edited time, converts the response results into a list, removes records that are not newer than the cursor, and yields non-empty batches. It stops when Notion says there are no more pages of results.

**Call relations**: `paginate` calls this directly for page and data source streams. `_blocks` and `_comments` also call it first because they need to discover pages before fetching each page’s blocks or comments. It uses `list_or_empty` to safely treat missing or malformed result lists as empty lists.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the blocks that make up Notion page bodies. It starts from every page and then asks for that page’s child blocks.

**Data flow**: It receives an HTTP client and an optional cursor for changed blocks. It first searches all pages, then reads each page ID. For every valid page ID, it calls `_block_children` to walk through that page’s block tree and yields the block batches it finds.

**Call relations**: `paginate` calls this when the sync is reading the `blocks` stream. It depends on `_search` to find pages and on `_block_children` to do the recursive block fetching.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through a Notion block tree and yields the child blocks it finds. It is careful not to go too deep and not to descend into block types that should be treated as their own records.

**Data flow**: It receives a client, a starting block ID, a current depth number, and an optional cursor. If the depth is beyond the safety limit, it stops. Otherwise it fetches child blocks through `_collection`, filters out blocks that are not newer than the cursor, yields any remaining blocks, and then repeats the process for child blocks that have their own children.

**Call relations**: `_blocks` calls this for each page ID. This function calls `_collection` to perform the actual paged `GET /blocks/{id}/children` requests, and it calls itself recursively to move deeper into nested blocks.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Notion pages. Since Notion comments are fetched per page, it first finds pages and then asks for comments on each one.

**Data flow**: It receives a client and an optional cursor based on comment creation time. It searches all pages, extracts each valid page ID, fetches comments for that page through `_collection`, filters out old comments if a cursor is present, and yields non-empty comment batches.

**Call relations**: `paginate` calls this for the `comments` stream. It uses `_search` to find pages and `_collection` to fetch the paginated comments for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged `results` list. It hides the repeated work of following Notion’s `next_cursor` values.

**Data flow**: It receives a client, an API path, and optional query parameters. It calls the parent connector’s cursor-page helper with Notion’s field names, page-size setting, and cursor parameter name. It yields each page of records as a list of dictionaries.

**Call relations**: `paginate` uses this directly for users. `_block_children` uses it for block children, and `_comments` uses it for page comments. It is the common paging mechanism for normal Notion `GET` collection endpoints.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a raw Notion record into readable text with a title and body. It is needed because Notion’s useful words are often buried inside rich-text arrays and type-specific fields, not in simple flat columns.

**Data flow**: It receives one raw record and the stream it came from. Based on the stream name, it extracts the best title and body text using helper functions for page titles, properties, blocks, comments, and users. It returns a pair: the short title and a formatted text document headed with the Notion stream name.

**Call relations**: The source framework calls this after records are fetched so they can be stored or indexed as prose. It calls `_page_title`, `_properties_text`, `_rich_text_text`, `_block_text`, `_str`, and `_user_text` depending on the record type. If it does not recognize the stream, it falls back to the parent connector’s rendering.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only when it already is one. It prevents accidental text like `None` or Python object representations from appearing in rendered content.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `render`, `_property_text`, `_block_text`, and `_user_text` use this when pulling optional text fields from Notion records. It acts as a small safety filter before text is included in output.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts readable words from Notion rich text. Notion stores formatted text as a list of small pieces, and this joins their plain-text parts into one normal string.

**Data flow**: It receives a value that should be a list of rich-text pieces. If it is not a list, it returns an empty string. If it is a list, it keeps each piece’s `plain_text` value when present, joins them together, trims surrounding whitespace, and returns the result.

**Call relations**: `render` uses this for comments and data source text. `_page_title`, `_property_text`, and `_block_text` also call it whenever they need to translate Notion rich-text fields into readable prose.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the human title of a Notion page. In Notion, the title is stored as one special property, so this function searches the properties to find it.

**Data flow**: It receives a page record. It reads the page’s `properties` dictionary, looks for the property whose type is `title`, extracts that property’s rich text, and returns the first non-empty title it finds. If the page has no usable title, it returns an empty string.

**Call relations**: `render` calls this when rendering page records. It delegates rich-text extraction to `_rich_text_text` so the page title becomes plain readable text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s visible properties into simple labeled lines. It makes database-style page fields readable, such as `Status: In progress` or `Owner: Alice`.

**Data flow**: It receives a page record. It reads the `properties` dictionary, asks `_property_text` to convert each supported property value into text, and builds one `name: value` line for each non-empty result. It returns all lines joined with newline characters.

**Call relations**: `render` calls this for page records after finding the title. It relies on `_property_text` to understand the different Notion property shapes.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property into readable text. It covers common property types like title, rich text, select lists, people, dates, numbers, URLs, emails, phone numbers, and checkboxes.

**Data flow**: It receives one property dictionary. It reads the property’s declared type, pulls the matching value field, and converts that value according to the type. It returns readable text for supported types and an empty string for unsupported or missing values.

**Call relations**: `_properties_text` calls this for each property on a page. It uses `_rich_text_text` for formatted text fields and `_str` for optional string fields such as names and dates.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from a Notion block, such as a paragraph, heading, list item, to-do item, or child-page title. Blocks are the pieces that make up a Notion page body.

**Data flow**: It receives a block record. It checks the block’s type, reads that type’s content section, and returns the best plain text. For to-do blocks, it prefixes the text with `[x]` or `[ ]` to show whether the task is checked.

**Call relations**: `render` calls this for records from the `blocks` stream. It uses `_rich_text_text` for normal block text and `_str` for child page or child database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion user record into simple text containing the user’s name and email when available. That makes user records useful in the same recallable text format as pages and comments.

**Data flow**: It receives a user record. It reads the top-level name and, if present, the nested person email. It keeps only real strings and joins the available parts with a newline.

**Call relations**: `render` calls this for the `users` stream. It uses `_str` to avoid including non-text values in the rendered user body.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Outlook communication
Reads Outlook email, contacts, calendar events, conversations, and folders through Microsoft Graph.

### `extensions/sources/ufo_ext_sources/providers/outlook.py`

`io_transport` · `source sync runs`

This connector is used when the system needs to copy a user’s Outlook information into its own storage. It talks to Microsoft Graph, which is Microsoft’s web API for mailbox and calendar data. The important trick here is incremental syncing: after the first full read, Outlook gives back a special “delta link,” like a bookmark that says “next time, start from the changes after this point.” Without this file, Outlook accounts could not be read reliably, and every sync would either miss data or waste time re-reading everything.

The file defines five streams: contacts, messages, conversations, events, and mail folders. Messages and contacts are read folder by folder, so their saved cursor is a small JSON map from folder ID to that folder’s delta link. Events use a moving calendar window, looking one year back and two years ahead. Conversations are not a real Graph object here; they are built by reading messages and grouping them by conversation ID, like sorting letters into piles by thread.

The connector also translates raw Microsoft fields into friendlier common fields. For example, it pulls a contact’s first email address into `email`, turns an event body from simple HTML into plain text, and adds `thread_id` to messages. If Microsoft rejects access with a permission error, the stream is marked as skipped instead of crashing the whole sync.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: This helper turns a Python date and time into the exact timestamp text that Microsoft Graph expects in filters. It makes sure the time is expressed in UTC, the shared world time standard.

**Data flow**: It receives a datetime value → converts it to UTC → returns a string such as `2024-01-01T12:00:00Z` that can be placed inside a Graph API query.

**Call relations**: The conversation and message sync paths call this when they need to start from a backfill cutoff date. It supplies the timestamp used in Microsoft Graph filter expressions.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: This helper removes simple HTML tags from text, mainly so calendar event descriptions can be stored as readable plain text. If the input is not text, it safely returns nothing.

**Data flow**: It receives any value → checks whether it is a string → replaces anything that looks like an HTML tag with a space → returns the cleaned and trimmed text, or `None` for non-text input.

**Call relations**: The `flatten` method uses this when preparing event records. It turns Graph’s event body content into a simpler description field.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper finds the first usable email address on a Microsoft contact record. Contacts can contain several addresses, but the system wants one convenient `email` field.

**Data flow**: It receives a contact dictionary → looks inside its `emailAddresses` list → reads each nested address field → returns the first non-empty email address, or `None` if none is found.

**Call relations**: The `flatten` method calls this while reshaping contact records. It relies on the shared `get_path` helper to safely read nested dictionary fields.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper chooses a useful phone number from a contact record. It prefers the mobile phone number, then falls back to the first business phone number.

**Data flow**: It receives a contact dictionary → checks `mobilePhone` first → if that is empty, scans `businessPhones` → returns the first usable phone number, or `None` if there is no phone number.

**Call relations**: The `flatten` method calls this when preparing contacts for the project’s common contact shape.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s outer paging entry used by the source framework. It accepts the normal sync inputs and forwards them into this connector’s Outlook-specific pagination logic.

**Data flow**: It receives an HTTP client, a stream description, an optional saved cursor, an optional user ID, and an optional backfill cutoff → passes the relevant pieces to `paginate` → returns the pages produced by `paginate`.

**Call relations**: The broader source runner calls this when it wants Outlook records. This method hands the work to `OutlookConnector.paginate`, adding support for the backfill cutoff used by mail-related streams.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the traffic director for Outlook syncs. Based on the stream name, it chooses the right reading method for conversations, messages, contacts, events, or mail folders.

**Data flow**: It receives the requested stream and current cursor → routes the request to the matching helper → yields pages of records and deletion markers. If Microsoft says access is unauthorized or forbidden, it raises a skip signal instead of treating the whole run as a hard failure.

**Call relations**: `paginate_source` calls this during a sync. It then delegates to `_conversation_pages`, `_message_delta_pages`, `_contact_delta_pages`, `_event_delta_pages`, or `_graph_delta_pages`, depending on what kind of Outlook data is being read.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This builds conversation records by reading messages and grouping them by Microsoft’s `conversationId`. Microsoft Graph is not used here as a direct conversation feed; the connector derives thread summaries from email messages.

**Data flow**: It receives an HTTP client, a saved cursor, and possibly a backfill cutoff → asks Graph for messages ordered by last modified time → groups messages by conversation ID → keeps the newest message data while preserving the earliest creation time → yields one list of conversation summaries.

**Call relations**: `paginate` calls this for the `conversations` stream. It uses `_graph_instant` when it needs to turn a backfill date into a Graph filter, and it reads pages through the base connector’s OData paging helper.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the shared engine for Microsoft Graph delta feeds. A delta feed reports both new or changed records and deleted records since the last saved bookmark.

**Data flow**: It receives an initial API path, an optional cursor, and optional query parameters → repeatedly requests Graph pages → separates normal records from items marked as removed → yields `StreamPage` objects containing records, deletions, and the next cursor. It stops when Graph gives a final delta link instead of another next page.

**Call relations**: `paginate` uses this directly for mail folders, and the message, contact, and event helpers use it for their own delta reads. It creates the standard page objects that the rest of the sync system understands.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads changed Outlook messages across all mail folders. Because Microsoft tracks message deltas separately per folder, this method keeps a separate saved bookmark for each folder.

**Data flow**: It receives an HTTP client, an optional JSON cursor map, and possibly a backfill cutoff → decodes the folder cursor map → lists all mail folders → reads each folder’s message delta feed → stamps each message with its folder ID → updates the cursor map → yields pages with records, deletions, and the newly encoded cursor map.

**Call relations**: `paginate` calls this for the `messages` stream. It relies on `_list_mail_folders` to discover folders, `_graph_delta_pages` to read each folder’s delta feed, `_decode_cursor_map` and `_encode_cursor_map` to preserve per-folder bookmarks, and `_graph_instant` for the first-run backfill filter.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads changed Outlook contacts from the default contacts area and from any contact folders. Like messages, contacts need a separate cursor per folder.

**Data flow**: It receives an HTTP client and an optional JSON cursor map → decodes the saved folder bookmarks → builds a folder list starting with the default contacts area → reads each folder’s contact delta feed → updates the cursor map → yields pages with records, deletions, and the encoded next cursor. If the default contacts delta endpoint is unavailable with a 400 or 404 error, it skips that default area and continues.

**Call relations**: `paginate` calls this for the `contacts` stream. It uses `_list_contact_folders` to find extra folders, `_graph_delta_pages` for the Microsoft delta protocol, and the cursor map helpers to save progress across folders.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads changed calendar events from a bounded calendar window. It looks from one year in the past to two years in the future, which keeps the event sync useful without asking for an unlimited calendar history.

**Data flow**: It receives an HTTP client and optional cursor → if this is a fresh delta walk, builds start and end date parameters around the current time → asks `_graph_delta_pages` to read `/me/calendarView/delta` → yields the resulting event pages.

**Call relations**: `paginate` calls this for the `events` stream. It delegates the actual Microsoft delta paging to `_graph_delta_pages`.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This asks Microsoft Graph for the user’s mail folder IDs. Those IDs are needed because message delta syncing happens folder by folder.

**Data flow**: It receives an HTTP client → pages through `/me/mailFolders` → collects each folder’s non-empty ID → returns a list of folder IDs.

**Call relations**: `_message_delta_pages` calls this before syncing messages. The returned folder IDs decide which message delta feeds will be visited.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This asks Microsoft Graph for the user’s contact folder IDs. Those IDs let the connector sync contacts outside the default contacts area.

**Data flow**: It receives an HTTP client → pages through `/me/contactFolders` → collects each folder’s non-empty ID → returns a list of folder IDs.

**Call relations**: `_contact_delta_pages` calls this before syncing contacts. The returned folder IDs are added after the default contacts area.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Microsoft Graph records into fields that are easier for the rest of the system to use. It keeps the original record but adds common names like `email`, `snippet`, `sent_at`, or `start_at` depending on the stream.

**Data flow**: It receives one raw record and its stream description → checks whether the record is a contact, message, event, or something else → adds stream-specific convenience fields → returns the enriched dictionary. For unknown or already simple streams, it returns the record unchanged.

**Call relations**: The source framework uses this after records have been fetched. It calls `_first_email` and `_phone` for contacts, `_strip_html` for event descriptions, and `get_path` to safely read nested Microsoft Graph fields.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: This turns a saved JSON cursor into a normal dictionary of folder IDs to delta links. It is deliberately forgiving, so a missing, broken, or wrongly shaped cursor becomes an empty map instead of crashing the sync.

**Data flow**: It receives a raw cursor string or `None` → tries to parse it as JSON → checks that it is a dictionary → keeps only non-empty string values → returns a clean folder-to-cursor dictionary.

**Call relations**: The message and contact delta methods call this at the start of their work. It gives them the per-folder bookmarks they need before reading Graph.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: This turns the connector’s folder cursor dictionary back into a JSON string that can be saved for the next sync. It returns nothing if there are no cursors to save.

**Data flow**: It receives a dictionary of folder IDs to delta links → if the dictionary is non-empty, serializes it as stable sorted JSON → returns that string; otherwise returns `None`.

**Call relations**: The message and contact delta methods call this after each page updates folder progress. The encoded string becomes the next cursor handed back to the source framework.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack workspace chat
Reads Slack users, channels, messages, threads, and senders into clean searchable workspace records.

### `extensions/sources/ufo_ext_sources/providers/slack.py`

`io_transport` · `source sync runs`

Slack does not hand over a whole workspace in one simple download. It gives lists in pages, uses special cursor tokens to ask for the next page, and sometimes reports errors inside a successful-looking response. This file is the adapter that understands those Slack habits.

The main class, SlackConnector, exposes several read-only streams: users, conversations, threads, messages, and message participants. For users and conversations, it walks Slack’s list APIs page by page and treats each run as a fresh snapshot, so missing users or channels can be marked as gone. For messages, it first gathers the readable channels, then walks each channel’s history separately. That matters because a very busy channel should not cause quiet channels to be skipped.

As messages come back, the file reshapes Slack’s raw data into simpler records. One raw Slack message may produce a message record, a thread summary, and a participant record. Deleted Slack messages are turned into delete markers. Messages from this app’s own live bot user are ignored so the system does not recall its own output as source material.

The file also draws an important line between “Slack refused this because permission is missing” and “something truly failed.” Missing permission can skip a stream or channel without crashing the whole sync.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error object when Slack says a request failed, even if the HTTP request itself looked successful. It keeps Slack’s error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives an error name and an optional needed permission → builds a readable message like “slack: missing_scope” → stores the error details on the exception for callers to inspect.

**Call relations**: _ok_or_raise calls this when Slack returns ok=false. The resulting error is later caught by higher-level code that decides whether the problem is a missing permission, a skipped channel, or a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard entry point the source framework uses to ask this connector for pages of Slack data. It mainly forwards the request to the connector’s main pagination method.

**Data flow**: It receives an HTTP client, a stream description, cursor state, the connector’s own Slack user id, and an optional backfill cutoff → passes those through unchanged → returns the pages produced by paginate.

**Call relations**: The wider sync framework calls this first. It hands off immediately to SlackConnector.paginate, which chooses the right Slack API path for the requested stream.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses how to read each Slack stream. Users and conversations are simple paged lists; message-related streams need a channel-by-channel history walk.

**Data flow**: It receives the stream being synced, saved cursor position, Slack client, bot user id, and optional backfill date → fetches the right Slack data, sometimes building user and channel lookups first → yields lists of records or cursor-aware stream pages back to the sync engine.

**Call relations**: paginate_source calls this as the main dispatcher. It calls iter_users for user pages, iter_conversations for channel pages, user_index to enrich messages with people data, and PartitionWalk to coordinate per-channel message history without losing position.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Lists the Slack channel ids that should be walked for message history. It is a small helper used by the per-channel history walker.

**Data flow**: It reads the already-built channel dictionary → yields one channel id at a time → gives PartitionWalk the set of independent channel partitions to process.

**Call relations**: SlackConnector.paginate defines this while setting up message streams. PartitionWalk uses it to know which channels should each have their own progress marker.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects PartitionWalk’s request for one channel’s pages to the Slack-specific code that actually reads that channel’s history. It supplies the channel details and user lookup needed to shape records.

**Data flow**: It receives a channel id and a time boundary from PartitionWalk → looks up the channel’s stored conversation data → returns the async page stream from _channel_pages.

**Call relations**: SlackConnector.paginate passes this helper into PartitionWalk. Whenever PartitionWalk is ready to fetch a slice of one channel, this helper hands the work to SlackConnector._channel_pages.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users, one API page at a time. It also reshapes each Slack user into the simpler user format expected by the system.

**Data flow**: It starts with no cursor → asks Slack users.list for a page → keeps valid user objects, flattens their profile fields, yields the page, then follows Slack’s next cursor until there are no more pages.

**Call relations**: SlackConnector.paginate calls this when syncing the users stream. SlackConnector.user_index also calls it to build a lookup table used while converting messages.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including public channels, private channels, direct messages, and group messages. It turns Slack’s channel objects into records with consistent names and flags.

**Data flow**: It starts with no cursor → asks Slack conversations.list for a page → extracts useful fields such as name, type, privacy, archive status, topic, and creation time → yields pages until Slack has no next cursor.

**Call relations**: SlackConnector.paginate calls this directly for the conversations stream and also uses it before message syncs to find which channels can be walked.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a dictionary of Slack users keyed by user id. Message conversion uses this to attach readable names and email addresses when possible.

**Data flow**: It reads all pages from iter_users → stores each valid user record under its id → returns the completed user lookup table.

**Call relations**: SlackConnector.paginate calls this before reading message-related streams. The resulting lookup is passed down into _channel_pages and then _message_page so messages and participants can be enriched.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one Slack channel’s message history within the time window requested by the partition walker. If this one channel cannot be read because of Slack permissions or state, it skips only that channel instead of failing the whole workspace sync.

**Data flow**: It receives a channel, a stream type, a time boundary, user lookup, and bot user id → sends conversations.history requests with the right oldest/latest limits and cursor → filters valid raw messages → converts each Slack page into a WalkPage → yields pages until the channel history page cursor ends.

**Call relations**: SlackConnector.paginate.channel_pages calls this for each channel that PartitionWalk wants to read. It calls _slack_post to talk to Slack, _message_page to turn raw messages into records, and _next_cursor to continue through Slack’s pages.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific stream being synced: messages, thread summaries, or message participants. It also notices Slack deletion events and reports deleted message ids for the messages stream.

**Data flow**: It receives raw Slack messages plus channel and user context → skips deletion notices after recording their target ids, drops messages from this app’s own bot user, flattens normal messages, derives thread and participant records, and calculates the newest and oldest Slack timestamps on the page → returns a WalkPage containing the chosen record type and timestamp span.

**Call relations**: _channel_pages calls this after each conversations.history response. It relies on _flatten_message, _conversation_thread_from_message, and _participant_for_message to fan one Slack page into the stream-specific records PartitionWalk will pass upward.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs a top-level Slack list request and turns missing-permission refusals into a clean stream skip. This prevents a workspace sync from looking like a hard failure when the Slack app simply lacks access.

**Data flow**: It receives an API path and query parameters → calls _slack_get → if Slack reports a known permission problem or HTTP 403, raises StreamSkipped; otherwise returns the response data or lets unexpected errors rise.

**Call relations**: iter_users and iter_conversations call this for Slack’s list APIs. It sits between those high-level readers and _slack_get, adding the policy for permission-related skips.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a Slack GET request and checks Slack’s own ok flag. Slack often reports application-level failure inside the JSON body, so this wrapper catches that pattern.

**Data flow**: It receives an HTTP client, path, and optional query parameters → uses the base connector’s GET helper to fetch JSON → passes the data to _ok_or_raise → returns confirmed-good Slack data.

**Call relations**: _enumerate calls this for users.list and conversations.list. It hands Slack’s response to _ok_or_raise so ok=false becomes a normal Python exception.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a Slack POST request and checks Slack’s own ok flag. It is used for Slack endpoints that expect request details in the body, such as channel history.

**Data flow**: It receives an HTTP client, path, and optional JSON body → uses the base connector’s POST helper → sends the returned data through _ok_or_raise → returns only responses Slack marked as successful.

**Call relations**: _channel_pages calls this when reading conversations.history. It delegates Slack-specific success checking to _ok_or_raise.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether Slack’s JSON response says the operation succeeded. This is needed because Slack can return HTTP 200 while still saying ok=false in the body.

**Data flow**: It receives decoded Slack response data → if ok is false, extracts the error code and optional needed scope and raises SlackApiError → otherwise returns the original data unchanged.

**Call relations**: _slack_get and _slack_post call this after network requests. When it raises SlackApiError, callers such as _enumerate and _channel_pages decide whether to skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s token for the next page of results. Without this, the connector would only read the first page of large workspaces or busy channels.

**Data flow**: It receives a Slack response dictionary → looks inside response_metadata.next_cursor → returns a non-empty cursor string, or None if there is no next page.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this after each Slack page. Its result decides whether those loops continue or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack’s ordinary Unix timestamps into ISO date strings, which are easier for the rest of the system to store and compare. A Unix timestamp is a number of seconds since 1970-01-01 UTC.

**Data flow**: It receives any value → rejects booleans and values that cannot be read as numbers → converts valid seconds into a UTC ISO timestamp string, or returns None for invalid input.

**Call relations**: iter_conversations uses this for channel creation times, and _flatten_user uses it for user update times.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a Python datetime into Slack’s special message timestamp string format. It pads the value so string comparisons keep the same order as time comparisons.

**Data flow**: It receives an optional datetime → if missing or before the Unix epoch, returns None → otherwise turns it into a fixed-width Slack-style timestamp string with six decimal places.

**Call relations**: SlackConnector.paginate calls this when setting the backfill floor for PartitionWalk. That floor tells message history walks how far back they are allowed to descend.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack’s message timestamp string into a normal UTC ISO timestamp string. This makes message times easier for downstream code and humans to read.

**Data flow**: It receives a Slack timestamp string or None → if empty or invalid, returns None → otherwise parses it as seconds and returns an ISO timestamp.

**Call relations**: _flatten_message calls this for message sent time. _conversation_thread_from_message calls it for thread creation, update, and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s nested user object into a flat, predictable user record. It chooses sensible display names and normalizes email addresses when available.

**Data flow**: It receives one raw Slack user dictionary → reads profile fields such as email, names, phone, title, timezone, bot flags, deletion flag, and update time → returns a clean dictionary with stable field names.

**Call relations**: iter_users calls this for each valid Slack member. It uses _first_text to choose the best available name and _unix_to_iso to convert Slack’s update time.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into the system’s message record format. It also filters out messages sent by this app’s own bot user so the source does not ingest itself.

**Data flow**: It receives a raw message, its conversation, a user lookup, and the connector’s own Slack user id → validates the timestamp and channel, skips self-bot messages, chooses text and thread id, adds sender information, snippet, and readable sent time → returns a message dictionary or None if the message should not be kept.

**Call relations**: _message_page calls this for each raw Slack message that is not a deletion notice. It uses _slack_ts_to_iso for time, _snippet for preview text, and _first_text to choose the best sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread summary record when a Slack message is part of a real thread. Standalone messages without replies do not become thread records.

**Data flow**: It receives an already-flattened message plus the raw Slack message and conversation → checks whether the message is a thread root with replies or a reply inside a thread → builds a summary with title, counts, privacy/archive flags, parent channel, and last-message time → returns the thread record or None.

**Call relations**: _message_page calls this after flattening each message. Its output is collected only when the requested stream is conversation_threads.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system later answer who took part in a Slack conversation or thread.

**Data flow**: It receives a flattened message and user lookup → chooses a handle from email or Slack user id → if no handle is available, returns None; otherwise returns a participant record tied to the message, channel, and thread.

**Call relations**: _message_page calls this for each kept message. It uses _first_text to choose the best sender handle and contributes records to the message_participants stream.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Labels a Slack conversation as a direct message, multi-person direct message, private channel, or public channel. This hides Slack’s several boolean flags behind one clearer field.

**Data flow**: It receives a raw Slack conversation dictionary → checks Slack’s type flags in priority order → returns a simple type string.

**Call relations**: iter_conversations calls this while shaping Slack channel objects into the system’s conversation records.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value buried inside nested dictionaries, such as a channel topic’s text. It avoids errors when Slack omits part of the structure.

**Data flow**: It receives a dictionary and a path of keys → walks one key at a time while the current value is still a dictionary → returns the found value, or None if the path cannot be followed.

**Call relations**: iter_conversations calls this to pull topic and purpose text out of Slack’s nested conversation fields.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first usable non-empty string from several possible values. It is a small helper for picking the best name, email, or handle when Slack provides many alternatives.

**Data flow**: It receives any number of values → checks them in order → returns the first string that still has text after trimming spaces, or None if none qualify.

**Call relations**: _flatten_user uses this for display and real names, _flatten_message uses it for sender handles, and _participant_for_message uses it for participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short, single-line preview of a Slack message’s text. This gives search and display code a compact summary without storing only the full body.

**Data flow**: It receives optional message text → returns None for empty text → collapses repeated whitespace into single spaces and cuts the result to the configured snippet length.

**Call relations**: _flatten_message calls this while building each message record, placing the result in the message’s snippet field.

*Call graph*: called by 1 (_flatten_message).

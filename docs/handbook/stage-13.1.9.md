# Knowledge, collaboration, scheduling, and community content connectors  `stage-13.1.9`

This stage is behind-the-scenes support for bringing outside team knowledge into the system. It does not create the main product experience by itself. Instead, it acts like a set of translators that visit other services, read what is there, and reshape it into clean records the rest of the system can store, search, and show.

The Airtable connector reads bases, tables, and records, but only in read-only mode, so it never changes the original Airtable data. The Calendly connector reads scheduling information such as users, event types, groups, scheduled meetings, and invitees. The Confluence connector pulls Atlassian spaces, pages, blog posts, comments, groups, and audit records, turning them into readable text. The Notion connector does the same for pages, databases, blocks, comments, and users. The Slack connector reads workspace people, channels, messages, threads, and participants. The Y Combinator connector imports selected YC guidance and limited directory-style searches, such as companies, founders, jobs, and posts, as shared searchable memory.

## Files in this stage

### Structured apps and scheduling
Read-only connectors that turn lightweight operational systems and scheduling data into searchable records.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `source sync`

Airtable is organized like a set of workspaces. First there are bases, then tables inside each base, then records inside each table. This connector walks that structure in order, like opening a filing cabinet, then each drawer, then each folder. That matters because Airtable does not provide one simple “give me everything” list. Without this file, the system would not know how to discover new Airtable tables automatically or how to fetch records with the right base and table context attached.

The connector defines three streams, which are categories of data the sync system can ask for: bases, tables, and records. When asked for bases, it calls Airtable’s metadata API and returns the list. When asked for tables, it first gets every base, then asks Airtable for the tables in each one. When asked for records, it gets every base, then every table, then reads records from each table in pages of up to 100 records. Airtable uses an opaque offset token for pagination, meaning the connector just passes the token back to Airtable rather than trying to interpret it.

Each table or record is stamped with extra context, such as the base ID or table ID, so later parts of the system can tell where it came from. The flatten step then turns Airtable’s raw responses into a steadier shape for downstream readers.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases available to the connected account. A base is Airtable’s top-level container, similar to a workbook or project.

**Data flow**: It receives an HTTP client that is already ready to talk to Airtable. It asks Airtable for `/meta/bases`, then pulls the `bases` list out of the response. It returns that list as plain dictionary-like records.

**Call relations**: This is the first discovery step used by `AirtableConnector.paginate`. The rest of the connector depends on it because tables and records can only be found after the connector knows which bases exist.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables that belong to one Airtable base. It also labels each table with the base it came from, so the table is not separated from its origin later.

**Data flow**: It receives an HTTP client and one base record. It reads the base’s `id`; if the ID is missing or not usable, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the `tables` list, adds base information such as `base_id` and `base_name` to each table, and returns the enriched list.

**Call relations**: This is called by `AirtableConnector.paginate` after bases have been discovered. In the tables stream, its results are gathered into pages. In the records stream, its table results become the starting points for reading records.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads records from one Airtable table, one page at a time. It adds base and table details to each record so every synced record can be traced back to its Airtable location.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks the table ID; if it is missing or invalid, it stops without yielding anything. Otherwise it requests records from Airtable using Airtable’s offset-based paging, with up to 100 records per page. For each non-empty page, it adds `base_id`, `table_id`, and `table_name`, then yields that page onward.

**Call relations**: This is called by `AirtableConnector.paginate` only for the records stream, after the connector has already found a base and one of its tables. It relies on the shared REST connector’s cursor-page helper for the repeated page requests, then hands each enriched page back to the main pagination flow.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for Airtable syncing. Depending on which stream the sync system asks for, it knows whether to return bases, tables, or records.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. It checks the stream name. For `bases`, it fetches bases and yields them as one page if any exist. For `tables`, it fetches bases, then tables for each base, collecting them into batches of about 100 before yielding. For `records`, it walks bases, then tables, then yields record pages from each table. If the stream name is not one this connector supports, it raises a skip signal instead of pretending it succeeded.

**Call relations**: This is the method the broader source-sync framework calls when it wants Airtable data. It coordinates the helper methods `_bases`, `_tables_for_base`, and `_records_for_table`, using them in the order Airtable’s structure requires.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Airtable records into a cleaner, more predictable shape for the rest of the system. This is where extra convenient fields, such as API URLs or normalized timestamps, are added.

**Data flow**: It receives one Airtable record and the stream it belongs to. For a base, it keeps the original data and adds a stable API URL. For a table, it adds the API URL for that base/table pair. For a record, it makes sure `fields` is a dictionary, copies the Airtable ID, and exposes `createdTime` as `created_at`. It returns the reshaped record without writing anything back to Airtable.

**Call relations**: After `paginate` has produced raw pages of Airtable data, the surrounding connector framework can call this function to prepare each item for storage or indexing. It does not fetch more data itself; it only reshapes the item it is given.


### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `source sync run`

Calendly’s API is organized around an account’s current organization, so this connector first asks Calendly who the authenticated user is and which organization they belong to. Without that step, most of the useful collections cannot be requested correctly. After it knows the organization, it fetches each supported stream in pages, like reading a long guest list one sheet at a time instead of all at once.

The file defines which Calendly streams exist, what key identifies each record, and which fields can be used as a progress marker for incremental syncing. Incremental syncing means the connector can ask for “only things updated or created after the last saved point,” instead of rereading everything every time.

Some Calendly data needs special treatment. Invitees are not fetched from one global endpoint; the connector first reads scheduled events, extracts each event’s ID from its URL, then asks Calendly for that event’s invitees. Membership records also contain a nested user object, and this file flattens that into name and email so a membership stays focused on the membership itself. The connector is read-only: it pulls data from Calendly using OAuth credentials, but it never writes changes back.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like piece out of a Calendly URI. It is used when the API gives a full web-style identifier but another API call needs only the last segment.

**Data flow**: It receives any value. If the value is a non-empty string, it removes a trailing slash if present and returns the text after the final slash. If the input is missing or not a string, it returns nothing.

**Call relations**: When invitees are being synced, CalendlyConnector._invitees uses this helper to turn each scheduled event’s URI into the event UUID needed for the invitee endpoint.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the authenticated account’s user profile. The most important reason it exists is to find the account’s current organization, which Calendly requires for most collection requests.

**Data flow**: It receives an HTTP client that is already ready to talk to Calendly. It requests /users/me, looks for the resource object in the response, and returns that object if it is shaped like a dictionary. If the response does not contain a usable user object, it returns an empty dictionary.

**Call relations**: CalendlyConnector.paginate calls this directly for the api_user stream. CalendlyConnector._org_stream also calls it before any organization-scoped stream, because those streams need the current organization value before they can fetch pages.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Calendly collection endpoint page by page. It hides Calendly’s paging details so the rest of the connector can simply receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly asks the shared REST paging helper for records under the response’s collection field, following Calendly’s next_page_token until there are no more pages. It yields each page as a list of record dictionaries.

**Call relations**: CalendlyConnector._org_stream uses this for organization-wide lists such as event types and scheduled events. CalendlyConnector._invitees uses it again for invitees under each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common path for Calendly streams that belong to an organization. It makes sure the connector has an organization, adds it to the request, applies an optional sync cursor, and returns the resulting pages.

**Data flow**: It starts with an HTTP client, an endpoint path, and optionally a saved cursor plus the Calendly query parameter that should receive that cursor. It fetches the current user, reads current_organization, and stops the stream with StreamSkipped if Calendly does not provide one. Otherwise it builds request parameters, pages through the collection, attaches organization context to each returned record, and yields each page.

**Call relations**: CalendlyConnector.paginate sends most streams through this function. CalendlyConnector._invitees also uses it to get the scheduled events that act as parents for invitee lookups. Inside, it relies on CalendlyConnector._current_user for the organization, CalendlyConnector._paginate_collection for paging, and with_context to preserve where the records came from.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches invitees for scheduled events, which requires a two-step walk through Calendly’s API. It first finds events, then asks for the invitees attached to each event.

**Data flow**: It receives an HTTP client and an optional cursor. It reads scheduled events for the organization, extracts each event UUID from the event URI, and skips events whose URI cannot be understood. For each valid event, it pages through that event’s invitees. If a cursor is present, it keeps only invitees whose created_at value is newer than the cursor. It yields non-empty invitee pages with extra context naming the parent event.

**Call relations**: CalendlyConnector.paginate calls this for the event_invitees stream. This function depends on CalendlyConnector._org_stream to discover events, _uuid_from_uri to get the event ID needed in the URL, CalendlyConnector._paginate_collection to read invitee pages, and with_context to attach the scheduled event details to each invitee record.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing point for reading Calendly streams. Given a stream name, it chooses the right Calendly endpoint and yields pages of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For api_user, it returns the current user as a single-record page. For organization streams, it calls the shared organization paging helper with the right endpoint and cursor parameter. For invitees, it calls the special invitee walker. If the stream name is unknown, it raises StreamSkipped so the sync can move on cleanly instead of pretending the stream worked.

**Call relations**: The broader source-sync machinery calls this when it needs records from a Calendly stream. It delegates simple user lookup to CalendlyConnector._current_user, organization-based collections to CalendlyConnector._org_stream, and nested invitee fetching to CalendlyConnector._invitees.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into friendlier records for the rest of the system. It keeps the original data but promotes important fields like name, email, title, start time, and location into predictable places.

**Data flow**: It receives one Calendly record and the stream it belongs to. Depending on the stream, it copies the record and adds or normalizes useful fields. For memberships, it safely reads the nested user object, copies out the member’s name and email, and removes the nested user object so later profile changes do not make the membership record look different. If the stream needs no special shape, it returns the record unchanged.

**Call relations**: After CalendlyConnector.paginate has supplied raw pages, the source framework can use this method to prepare each record for storage or indexing. It uses dict_or_empty when reading nested membership user data so missing or malformed user details do not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### Collaborative knowledge bases
Connectors that extract human-readable pages, databases, comments, and related workspace metadata from knowledge platforms.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `source sync`

Confluence stores page content as structured XHTML, which is a web-style markup format full of tags. If the system saved that raw markup, recall would be noisy and hard to read. This file solves that by fetching Confluence records through Atlassian’s API, shaping them into consistent records, and rendering wiki content as plain prose.

The connector first asks Atlassian which Confluence sites the current authorization grant can reach. A single grant may cover more than one site, so each stream fans out across all reachable site IDs. For each site, it calls the right Confluence endpoint and walks through results in pages of 50 items. Some streams are incremental, meaning the system remembers a previous “watermark” timestamp and only keeps records newer than that. Confluence does not provide a server-side “only changed since then” filter here, so the connector still reads pages and filters locally.

The file also protects against common permission problems. If Confluence replies that access is unauthorized or forbidden, the stream is marked as skipped rather than crashing the whole sync.

Finally, records are flattened and rendered. Page-like content gets a site-scoped ID so records from two Confluence sites cannot collide. Storage-format HTML is passed through a small text extractor that drops tags, keeps readable words, and adds line breaks around blocks, like turning a decorated webpage into clean notes.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the readable body content inside a Confluence record. It prefers the storage-format body, then falls back to the view-format body, and returns nothing if neither is usable text.

**Data flow**: It receives one record as a nested dictionary-like object. It looks inside paths such as body.storage.value and body.view.value, checks whether the found value is a non-empty string, and returns that string or None.

**Call relations**: ConfluenceConnector.flatten calls this when preparing pages, blog posts, and comments. It uses get_path so flatten does not need to know every nested dictionary step by hand.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches batches of records for one Confluence stream across every Confluence site the authorization grant can access. It is the main read loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It finds the API path for that stream, asks _sites for reachable Confluence sites, then asks _offset_results for batches from each site. Before yielding each batch, it adds site context such as cloud_id and site_url. If Confluence refuses access with a permission-related status, it turns that into a skipped stream instead of a failed run.

**Call relations**: The source-sync framework calls this when it needs records for a stream. This function delegates site discovery to ConfluenceConnector._sites, record paging and cursor filtering to ConfluenceConnector._offset_results, and context attachment to with_context. If access is refused, it creates StreamSkipped so the wider run can record a skip.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Discovers which Atlassian Confluence sites the current authorization grant can reach. This matters because all later Confluence API calls must be scoped to a specific site ID.

**Data flow**: It receives an HTTP client, calls Atlassian’s accessible-resources endpoint, reads the JSON response if there is content, and returns it as a list. If the response is not a list-shaped value, list_or_empty safely turns it into an empty list.

**Call relations**: ConfluenceConnector.paginate calls this before reading any stream data. The returned site records supply the cloud_id that paginate uses to build site-specific Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through a Confluence collection one page at a time using start and limit query parameters. It also applies local cursor filtering for incremental streams.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the name of the cursor field. It repeatedly requests a page of results, pulls the list from the response, filters out records older than or equal to the saved cursor when needed, yields non-empty batches, and stops when there are no records or Confluence no longer provides a next-page link.

**Call relations**: ConfluenceConnector.paginate calls this for each reachable site. Inside, it uses records_at to find the response’s results list and get_path to read nested cursor fields and the _links.next marker.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Confluence API records into the flatter shape the rest of the system expects. It adds common fields such as title, body, URL, creation time, and parent IDs where they make sense.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream, it copies the original fields and adds clearer fields for recall and syncing. It builds browser URLs from site_url plus Confluence’s web UI link, extracts page-like body text, scopes most IDs with the site’s cloud_id, and lifts nested cursor values like version.createdAt onto a flat key.

**Call relations**: The connector framework uses this after records are fetched and before they are stored or rendered. It calls _body_text for page-like bodies and get_path for nested values such as web links, author IDs, and version timestamps.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Creates a human-readable title and text body for a Confluence record. This is what makes synced Confluence content useful for recall instead of leaving it as raw API JSON or XHTML.

**Data flow**: It receives a flattened record and its stream description. For pages, blog posts, comments, and spaces, it chooses a title, extracts readable text from Confluence HTML-like content, builds a heading, and returns both the title and final rendered text. For other streams, it falls back to the parent connector’s default rendering.

**Call relations**: The source framework calls this when it needs display or recall text for a record. It calls _str to safely treat only real strings as titles, get_path to find nested space descriptions, and _StorageTextExtractor.extract to turn Confluence markup into plain text.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Sets up a small parser that collects readable text from Confluence storage-format XHTML. It starts with an empty list of text pieces.

**Data flow**: It receives no outside data beyond the new object being created. It initializes the underlying HTML parser with automatic character-reference conversion, then prepares an internal list where later parser callbacks will store text and line breaks.

**Call relations**: _StorageTextExtractor.extract creates this parser before feeding it raw Confluence markup. The parser’s later callback methods add data into the list initialized here.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts a raw Confluence HTML-like body into clean plain text. It is the public shortcut for using the extractor.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise, it creates a parser, feeds the raw markup into it, then returns the parser’s cleaned text output.

**Call relations**: ConfluenceConnector.render calls this when rendering pages, blog posts, comments, and space descriptions. It relies on the parser lifecycle: __init__ prepares storage, HTMLParser invokes the tag and data callbacks while feed runs, and _text produces the final string.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Keeps the actual words found between markup tags. This is where readable page text is preserved.

**Data flow**: It receives a text fragment from the HTML parser. It appends that fragment to the parser’s internal list, leaving cleanup and line joining for a later step.

**Call relations**: This method is called by the HTML parsing machinery while _StorageTextExtractor.extract feeds it Confluence markup. The collected pieces are later used by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag begins. This helps paragraphs, list items, table cells, and headings stay readable instead of running together.

**Data flow**: It receives a tag name and its attributes from the parser. If the tag is one of the known block tags, it appends a newline marker to the internal text list; otherwise it ignores the tag and all attributes.

**Call relations**: The HTML parser calls this during _StorageTextExtractor.extract. The newline markers it adds are later cleaned up by _StorageTextExtractor._text into tidy line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag ends. This gives the extracted text natural separation around paragraphs, headings, table rows, and similar blocks.

**Data flow**: It receives a closing tag name from the parser. If that tag is considered block-like, it appends a newline marker to the internal list; otherwise it does nothing.

**Call relations**: The HTML parser calls this as markup is fed by _StorageTextExtractor.extract. Together with handle_starttag and handle_data, it builds the raw pieces that _StorageTextExtractor._text cleans into final prose.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Turns the collected parser pieces into neat plain text. It removes extra spacing and blank lines while keeping meaningful line breaks.

**Data flow**: It reads the parser’s internal list of text fragments and newline markers. It joins them, splits by newline, collapses repeated spaces within each line, drops empty lines, and returns the cleaned string.

**Call relations**: _StorageTextExtractor.extract calls this after the raw markup has been parsed. It is the final cleanup step after handle_data, handle_starttag, and handle_endtag have collected the pieces.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. It prevents titles from accidentally becoming numbers, dictionaries, or other non-text values.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this when choosing titles for rendered records. That keeps rendering simple and avoids treating non-string API values as display text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `during Notion source sync`

Notion stores information in a shape that is easy for its app to use but not easy for a search or recall system to read directly. A page title may be hidden inside a special property, page body text is split across nested blocks, and comments and user details each have their own format. This connector acts like a careful translator: it asks Notion for each kind of object, follows Notion's paging rules, and then converts the useful human-facing parts into plain prose.

The file defines the Notion streams the sync system can read: users, pages, data sources, comments, and blocks. Pages and data sources are found through Notion's search endpoint, sorted by edit time so later runs can skip old records using a saved timestamp. Blocks are gathered by walking through each page's block tree, like opening folders inside folders, but with a depth limit so a strange or very deep page cannot trap the sync forever. Comments are fetched page by page. Users come from a simple users list.

If Notion refuses access because the integration lacks permission, the connector marks that stream as skipped rather than treating the whole run as broken. The file also overrides rendering so synced records look like what a person would read in Notion, instead of raw JSON.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Notion and adds the Notion API version header. Notion requires this header so it knows which version of its API rules the connector expects.

**Data flow**: It receives a base URL and a credential object. It first lets the shared REST connector create the normal web client, then adds the Notion-Version header to every request made by that client. It returns the prepared client ready to call Notion.

**Call relations**: This is part of the setup inherited from the general REST connector. Once the client exists, the pagination methods use it to make all Notion API requests with the correct version attached.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Notion stream. Given a stream such as pages, comments, or users, it chooses the right Notion API path and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor, which is usually a timestamp from a previous sync. It checks the stream name, calls the matching helper, and passes along each batch of records it receives. If Notion replies with an access-denied status, it turns that into a stream skip instead of a hard failure.

**Call relations**: The sync system calls this when it wants records for a Notion stream. It hands work to _collection for users, _search for pages and data sources, _comments for comments, and _blocks for page body blocks. If the stream is unknown or access is refused, it raises StreamSkipped so the wider sync can record that outcome cleanly.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Notion for pages or data sources and returns them in batches. It also applies the saved edit-time cursor itself because Notion does not offer a direct 'only changes since this time' search option.

**Data flow**: It receives the HTTP client, the kind of Notion object to search for, and an optional cursor timestamp. It repeatedly sends a search request sorted by last edited time, turns the response's results into a safe list, removes records that are not newer than the cursor, and yields any remaining records. It follows Notion's next cursor until there are no more pages of results.

**Call relations**: paginate calls this directly for pages and data sources. _blocks and _comments also call it first to discover which pages exist before fetching each page's body blocks or comments. It uses list_or_empty to guard against missing or oddly shaped response data.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the block content that makes up the body of Notion pages. Without it, the sync would know that pages exist but would miss much of what people actually wrote inside them.

**Data flow**: It receives the HTTP client and an optional cursor timestamp. It first searches for all pages, then takes each page ID and asks _block_children to walk that page's block tree. It yields every batch of blocks produced by that recursive walk.

**Call relations**: paginate calls this when the requested stream is blocks. It relies on _search to find pages and on _block_children to do the deeper work of reading each page's nested content.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the child blocks under one Notion block or page, including nested children. It is the part that walks through a page body in depth, while avoiding unsafe or separate areas such as child pages and databases.

**Data flow**: It receives the HTTP client, a block ID, the current depth in the tree, and an optional cursor timestamp. If the depth is too large, it stops. Otherwise it fetches the block's children, filters out blocks that are not newer than the cursor, yields the newer ones, and then repeats the process for child blocks that can safely be opened.

**Call relations**: _blocks starts this process for each page ID. The function calls _collection to fetch one level of children from Notion, then calls itself again for allowed nested blocks, like walking a branching outline one section at a time.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers comments attached to Notion pages. Comments are separate from the page body, so they need their own page-by-page lookup.

**Data flow**: It receives the HTTP client and an optional cursor timestamp. It searches for pages, takes each valid page ID, asks Notion for comments on that page, filters out comments older than or equal to the cursor, and yields any remaining comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages first and _collection to page through the comments endpoint for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a list of results with a next-page cursor. It keeps the rest of the connector from repeating the same paging pattern.

**Data flow**: It receives the HTTP client, an API path, and optional query parameters. It asks the base REST machinery to fetch pages of results, using Notion's result field, next cursor field, cursor parameter, and page size parameter. It yields each batch of records it gets back.

**Call relations**: paginate uses this directly for users. _block_children uses it for block children, and _comments uses it for comment lists. It delegates the low-level cursor paging to the shared _get_cursor_pages method from the parent connector.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion records into readable text for recall or search. It matters because raw Notion API data is full of nested fields, while users expect to find the title, body text, comment text, or user name.

**Data flow**: It receives one Notion record and the stream it came from. Depending on the stream, it extracts a title and body using helper functions for pages, properties, rich text, blocks, comments, or users. It then builds a simple heading and returns both the short title and the full readable text.

**Call relations**: The broader sync system calls this after records are fetched, when it needs a human-readable representation. It calls _page_title and _properties_text for pages, _rich_text_text for data sources and comments, _block_text for blocks, _user_text for users, and _str wherever a safe string is needed.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely returns a value only if it is already a string. It prevents unexpected Notion values, such as missing fields or objects, from being accidentally printed as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: render, _block_text, _property_text, and _user_text use this when they read optional Notion fields. It is a small guardrail that keeps the text output clean.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts plain readable text from Notion's rich text format. Notion stores formatted text as a list of small pieces, and this function joins the visible words together.

**Data flow**: It receives a value that may be a list of rich text pieces. If it is not a list, it returns an empty string. If it is a list, it looks for dictionary items with a plain_text string, joins those strings, trims extra space, and returns the result.

**Call relations**: render uses it for comments and data source fields. _page_title, _property_text, and _block_text use it whenever they need to turn Notion rich text into normal text.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the visible title of a Notion page. Page titles are stored as a special property, not as a simple top-level field.

**Data flow**: It receives a page record. It looks inside the page's properties, finds the property whose type is title, converts that rich text title to plain text, and returns the first non-empty title it finds. If the page has no usable title property, it returns an empty string.

**Call relations**: render calls this when preparing page records. It uses _rich_text_text to translate the title from Notion's rich text pieces into normal text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page's properties into a simple list of readable lines. It makes database-like fields such as status, dates, people, and checkboxes visible in the rendered page text.

**Data flow**: It receives a page record. It reads the properties dictionary, asks _property_text to convert each property value into text, and keeps only properties that produce non-empty text. It returns the result as lines in the form 'property name: value'.

**Call relations**: render calls this for page records after finding the page title. It delegates the details of each property type to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property into plain text based on the property's type. It knows how to read common property types such as title, rich text, select, people, dates, numbers, links, email, phone number, and checkbox.

**Data flow**: It receives one property dictionary. It checks the property's type, pulls the matching value field, and converts that value into a readable string. Unknown or unsupported property types become an empty string.

**Call relations**: _properties_text calls this for each page property. It uses _rich_text_text for text-like Notion fields and _str for optional names, dates, and other string fields.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from a Notion block. Blocks are the building pieces of a page body, such as paragraphs, headings, list items, to-dos, code blocks, and child page links.

**Data flow**: It receives a block record. It looks up the block's type-specific content, then extracts text from that content. For child pages and child databases it returns the title; for to-do blocks it prefixes the text with a checked or unchecked marker; for other rich-text blocks it returns the joined plain text.

**Call relations**: render calls this for records from the blocks stream. It uses _rich_text_text for normal block text and _str when reading child page or child database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion user record into readable text. It keeps the most useful identity details: the user's name and email address when present.

**Data flow**: It receives a user record. It reads the top-level name and, if the user has a person section, the email address inside it. It returns the non-empty parts joined on separate lines.

**Call relations**: render calls this for user records. It uses _str so missing or non-string name and email fields become harmless empty text instead of noisy output.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Conversation and community content
Sources that sync workspace conversations and curated community content into shared searchable memory.

### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `source sync runs`

Slack does not hand over a whole workspace in one simple download. Its API returns results in pages, uses cursors to say “there is more,” and sometimes reports errors inside a normal-looking HTTP response. This file wraps those Slack quirks so the rest of the system can treat Slack like a set of steady streams.

The connector exposes five streams: users, conversations, conversation threads, messages, and message participants. Users and conversations are read as full snapshots each run. That means if a user or channel disappears from what the integration can see, the system can mark it as missing. Messages work differently. The connector first lists readable channels, then walks each channel’s message history from newest to oldest. It keeps each channel as its own partition, like giving every channel its own bookmark, so a busy channel cannot cause a quiet channel to be skipped.

As pages arrive, the file reshapes Slack’s raw objects into simpler records: user profiles, channel summaries, message rows, thread rows, and sender participant rows. It also treats permission problems carefully. If the whole workspace cannot be listed because the Slack app lacks permission, the stream is skipped rather than counted as a crash. If one channel refuses message access, only that channel is skipped.

#### Function details

##### `SlackApiError.__init__`  (lines 88–92)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: This builds a clear error object for Slack failures that are reported inside Slack’s JSON response instead of as a normal HTTP error. It keeps Slack’s error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives a Slack error name and an optional needed permission. It turns those into a readable message like “slack: missing_scope,” stores the error details on the exception, and returns an exception object ready to be raised.

**Call relations**: The shared Slack response checker calls this when Slack says `ok=false`. Higher-level connector methods then catch this error and decide whether the problem means “skip this stream or channel” or “raise a real failure.”

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate`  (lines 100–137)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main doorway the sync system uses to ask Slack for records from one stream. It decides whether to list users, list conversations, or walk channel message history.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For user and conversation streams, it yields pages directly from the listing helpers. For message-related streams, it first builds a user lookup and a channel list, then uses a partitioned walk to read each channel’s history and yields stream pages. If the stream is unknown, it reports that the stream is skipped.

**Call relations**: The wider source-sync framework calls this when it needs Slack data. This method delegates simple lists to `iter_users` and `iter_conversations`, prepares shared context with `user_index`, and hands channel-by-channel message reading to `PartitionWalk` through its nested helper functions.

*Call graph*: calls 4 internal fn (__init__, iter_conversations, iter_users, user_index); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 120–122)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: This small nested helper supplies the list of Slack channel IDs that should be walked for message history. It is used so the partition walker can treat each channel as a separate unit of progress.

**Data flow**: It reads the already-built channel dictionary and yields one channel ID at a time. It does not return a final collection; it streams the IDs as the partition walker asks for them.

**Call relations**: It exists inside `SlackConnector.paginate` and is passed into `PartitionWalk`. The walker calls it to learn which channels need message pages.


##### `SlackConnector.paginate.channel_pages`  (lines 124–125)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: This nested helper tells the partition walker how to fetch pages for one specific channel. It connects a channel ID and a saved time boundary to the actual Slack history-reading method.

**Data flow**: It receives a channel ID and a partition bound, looks up that channel’s conversation details, and returns the async page stream produced by `_channel_pages`. The output is a sequence of walk pages for that channel.

**Call relations**: It is created inside `SlackConnector.paginate` and handed to `PartitionWalk`. Whenever the walker chooses a channel to advance, it calls this helper, which passes control to `_channel_pages`.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 139–155)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads all visible Slack users, one API page at a time. It also reshapes each raw Slack member into a simpler user record the system can store.

**Data flow**: It starts with no Slack cursor, asks `/api/users.list` for up to 200 members, filters out malformed entries, flattens valid users, yields a page if there are records, then follows Slack’s next cursor until there are no more pages.

**Call relations**: `SlackConnector.paginate` calls this directly for the users stream. `user_index` also calls it when message processing needs a lookup table from Slack user ID to user details.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 157–197)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads all visible Slack conversations, including public channels, private channels, group messages, and direct messages. It turns Slack’s detailed channel objects into consistent conversation records.

**Data flow**: It repeatedly calls `/api/conversations.list` with a cursor and channel-type filters. For each valid channel, it copies important fields, derives a plain conversation type, pulls nested topic and purpose text, converts creation time to an ISO timestamp, yields the page, and continues until Slack gives no next cursor.

**Call relations**: `SlackConnector.paginate` calls this for the conversations stream. The same method is also used before message syncing so the connector knows which non-archived channels can be walked.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 199–206)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: This builds a quick lookup table of Slack users by ID. Message conversion uses it to attach readable sender information, such as email or display name, to messages and participants.

**Data flow**: It reads every user page from `iter_users`, takes each user’s ID, and stores the user record in a dictionary keyed by that ID. The result is a map from Slack user ID to flattened user details.

**Call relations**: `SlackConnector.paginate` calls this before reading message-related streams. The resulting lookup is passed down into channel and message conversion so those lower-level functions can enrich message records.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 208–248)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]]) -> AsyncIterator[WalkPage]
```

**Purpose**: This reads one Slack channel’s message history in safe, bounded slices. It is careful about bookmarks so repeated sync runs do not miss messages posted between partial runs.

**Data flow**: It receives a channel, a stream type, a time bound, and the user lookup. It builds Slack `conversations.history` requests, using `oldest` when reading newer messages and `latest` when backfilling older messages. It converts each raw Slack page into the requested record type through `_message_page`, yields those walk pages, follows Slack’s next cursor, and skips only this channel if Slack refuses access to it.

**Call relations**: `PartitionWalk`, through the nested `channel_pages` helper in `paginate`, calls this for each channel. It calls `_slack_post` to talk to Slack, `_message_page` to reshape raw messages, and reports `PartitionSkipped` when one channel cannot be read.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 250–292)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]]) -> WalkPage
```

**Purpose**: This turns one raw Slack message-history page into records for exactly one message-derived stream. The same Slack page can become thread records, message records, or participant records depending on what the sync asked for.

**Data flow**: It receives the stream description, channel details, raw Slack messages, and the user lookup. It ignores deletion marker messages except to record deleted message IDs for the messages stream, flattens normal messages, derives thread summaries when relevant, derives sender participant records when possible, and records the newest and oldest Slack timestamps in the page. It returns a `WalkPage` containing records and the timestamp span.

**Call relations**: `_channel_pages` calls this after every Slack history response. This function coordinates the smaller conversion helpers: `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message`.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 294–314)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This wraps top-level Slack list calls, such as listing users or conversations, with permission-aware error handling. If Slack says the app lacks the broad permission needed to list the stream, the sync marks the stream as skipped instead of failed.

**Data flow**: It receives an HTTP client, a Slack API path, and query parameters. It calls `_slack_get`; if Slack reports a known permission refusal or HTTP 403, it raises a stream-skip signal. Otherwise it returns Slack’s parsed response or lets unexpected errors continue upward.

**Call relations**: `iter_users` and `iter_conversations` call this for their paged list requests. It sits between those listing loops and the lower-level `_slack_get` request helper.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 316–319)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This performs a Slack GET request and applies Slack-specific success checking. It hides Slack’s habit of putting errors inside a successful-looking response body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It sends the GET request through the base REST connector, then passes the returned JSON-like data to `_ok_or_raise`. The result is either confirmed-good Slack data or an exception.

**Call relations**: `_enumerate` calls this for Slack list endpoints. It relies on `_ok_or_raise` to translate Slack’s `ok=false` responses into `SlackApiError`.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 321–324)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This performs a Slack POST request and applies the same Slack-specific success check as the GET helper. It is used for Slack endpoints that expect request data in the body.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST request through the base REST connector, checks the response with `_ok_or_raise`, and returns confirmed-good Slack data.

**Call relations**: `_channel_pages` calls this to read `conversations.history`. Like `_slack_get`, it depends on `_ok_or_raise` to detect Slack errors hidden in normal HTTP responses.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 327–332)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This checks Slack’s response body for Slack’s own success flag. It turns `ok=false` into a `SlackApiError` so callers do not accidentally treat a failed Slack call as valid data.

**Data flow**: It receives a parsed Slack response dictionary. If the response says `ok` is false, it extracts the error name and optional needed permission, then raises `SlackApiError`. Otherwise it returns the original data unchanged.

**Call relations**: Both `_slack_get` and `_slack_post` call this immediately after network requests. Its errors are later interpreted by `_enumerate` for whole-stream skips and by `_channel_pages` for per-channel skips.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 335–340)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: This extracts Slack’s “next page” token from a response. It lets paging loops know whether they should make another request.

**Data flow**: It receives a Slack response dictionary, looks inside `response_metadata.next_cursor`, and returns the cursor only if it is a non-empty string. If the cursor is missing or empty, it returns nothing.

**Call relations**: `iter_users`, `iter_conversations`, and `_channel_pages` call this at the end of each page. Those callers stop their loops when it returns no cursor.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 343–350)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: This converts ordinary Unix timestamps, meaning seconds since 1970, into readable ISO date strings. It protects the rest of the code from bad or boolean timestamp values.

**Data flow**: It receives any value, rejects booleans, tries to read the value as seconds, and converts it to a UTC ISO timestamp string. If conversion fails, it returns nothing.

**Call relations**: `iter_conversations` uses it for channel creation times, and `_flatten_user` uses it for user update times. It is one of the small cleanup helpers that make Slack records consistent.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts_to_iso`  (lines 353–359)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: This converts Slack message timestamps into readable ISO date strings. Slack message timestamps are strings like seconds with a decimal part, so they need their own small conversion helper.

**Data flow**: It receives a Slack timestamp string or nothing. If there is a usable value, it converts it to a UTC ISO timestamp; if the value is missing or invalid, it returns nothing.

**Call relations**: `_flatten_message` uses it for message sent times. `_conversation_thread_from_message` uses it for thread creation, update, and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 362–389)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This reshapes one raw Slack member object into the cleaner user shape stored by the system. It picks useful identity fields and normalizes details like email and display name.

**Data flow**: It receives one raw Slack user dictionary. It reads the nested profile when present, trims and lowercases email, chooses the best available display and real names, copies profile fields, converts the update timestamp, and returns a new user record dictionary.

**Call relations**: `iter_users` calls this for every valid Slack member in a users page. It uses `_first_text` to choose names and `_unix_to_iso` to clean up timestamps.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 392–427)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: This reshapes one raw Slack message into the system’s standard message record. It adds channel context, thread identity, readable sender fields, and a short text preview.

**Data flow**: It receives a raw Slack message, its conversation record, and the user lookup. If the message lacks a valid timestamp or channel ID, it returns nothing. Otherwise it builds a stable message ID, chooses the thread ID, converts the sent time, makes a snippet from the text, chooses sender handle and display name, and returns the message record.

**Call relations**: `_message_page` calls this for each non-deletion raw message. It uses `_slack_ts_to_iso`, `_snippet`, and `_first_text` to produce cleaner fields for downstream storage.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 430–460)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: This derives a thread summary from a Slack message when that message is part of a real thread. It avoids creating thread records for ordinary one-off messages.

**Data flow**: It receives a flattened message, the original raw Slack message, and conversation details. It checks whether the message is a thread root with replies or a reply inside a thread. If so, it builds a thread record with title, counts, privacy/archive status, timestamps, and parent channel ID; otherwise it returns nothing.

**Call relations**: `_message_page` calls this after flattening each message. The thread records it produces are used only when the requested stream is `conversation_threads`.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 463–483)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: This creates a participant record for the sender of a message. It lets the system answer questions like who took part in a message or thread.

**Data flow**: It receives a flattened message and the user lookup. It chooses a sender handle, preferring email when known and falling back to Slack user ID. If no handle can be found, it returns nothing. Otherwise it builds a participant record tied to the message, channel, and thread.

**Call relations**: `_message_page` calls this for each flattened message. The participant records it produces are returned when the requested stream is `message_participants`.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 486–493)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: This translates Slack’s many channel flags into one simple conversation type label. It gives the rest of the system a stable category such as direct message, group direct message, private channel, or public channel.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s boolean flags in priority order and returns a plain string type.

**Call relations**: `iter_conversations` calls this while building conversation records from Slack channel pages.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 496–502)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: This safely pulls a value from nested dictionaries, such as a channel topic’s text. It prevents errors when Slack omits part of the expected structure.

**Data flow**: It receives a dictionary and a path of keys. It walks through the keys one by one; if the current value stops being a dictionary, it returns nothing. If the full path exists, it returns the final value.

**Call relations**: `iter_conversations` uses this to read nested channel `topic.value` and `purpose.value` fields without special-case error checks.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 505–509)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: This chooses the first useful text value from a list of candidates. It is used when Slack may provide the same human idea, like a name or handle, in several different fields.

**Data flow**: It receives any number of values. It returns the first value that is a string and still has content after trimming whitespace. If none qualify, it returns nothing.

**Call relations**: `_flatten_user`, `_flatten_message`, and `_participant_for_message` use this to pick display names, real names, and sender handles consistently.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 512–516)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: This turns message text into a short preview. It removes extra whitespace and caps the result so stored snippets stay compact.

**Data flow**: It receives optional text. If text is missing, it returns nothing. Otherwise it collapses runs of whitespace into single spaces, cuts the result to the snippet length limit, and returns the preview if anything remains.

**Call relations**: `_flatten_message` calls this when building the message record’s `snippet` field. That snippet can later be reused for thread titles and search previews.

*Call graph*: called by 1 (_flatten_message).


### `extensions/yc/ufo_ext_yc/source.py`

`io_transport` · `source sync and tool request handling`

This file is the bridge between UFO’s source-sync system and YC’s own search tools. Without it, a workspace could not safely and repeatably import YC manuals, startup library material, or search results from YC directories into UFO memory.

The file defines a configuration model that says which YC collection to read, whether a search query is needed, how many results to bring in, and how often to refresh. It then defines YcSource, which performs the actual sync. On each fetch, it first checks a saved cursor, which is like a “last synced at” receipt. If the data was refreshed recently enough, it skips work. Otherwise, it asks the YC runner to run a YC search command, page by page, with a timeout so a stuck external request does not block forever.

YC returns results as CSV text wrapped in a JSON tool response. This file checks that the response is for the expected search, verifies the row counts, turns each CSV row into a Page object, and gives each page a stable digest, which is a fingerprint used to notice changes. Very large page bodies are clipped to a fixed byte limit so one huge record cannot overwhelm the source system.

The yc_index tool is the user-facing entry point for directory searches. It checks that YC is connected, registers the requested search as a shared source, and tells the user that syncing has begun.

#### Function details

##### `YcSourceConfig.validate_collection`  (lines 79–88)

```
def validate_collection(self) -> 'YcSourceConfig'
```

**Purpose**: This checks that a YC source configuration makes sense before it is used. Guidance collections are fixed libraries and must not have a search query, while directory collections are searches and therefore must have one.

**Data flow**: It receives a filled-in YcSourceConfig object. It looks at the chosen collection, query, and result limit, rejects combinations that do not make sense, and fills in a default result limit for searchable directory collections when none was supplied. It returns the same config object after those checks and small fixes.

**Call relations**: This is run automatically by Pydantic, the validation library, whenever code builds or validates a YcSourceConfig. That means later sync code can assume the config already follows the rules instead of re-checking them every time.


##### `YcSource.fetch`  (lines 138–155)

```
async def fetch(self, config: YcSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main sync entry for a YC source. It decides whether the source needs refreshing, fetches fresh pages when needed, and returns the result in the format UFO’s source system expects.

**Data flow**: It receives a source config, an optional saved cursor from the previous sync, and authentication information. It compares the cursor time with the current time; if the refresh window has not expired, it returns no new pages and keeps the cursor. If a refresh is needed, it calls the collection fetcher inside a timeout, converts a missing YC credential into a clean “stream skipped” message, and returns the fetched pages plus a new cursor timestamp.

**Call relations**: The source-sync engine calls this when it wants to update YC content. When real work is needed, this function hands off to YcSource._fetch_collection to talk to YC and build pages, then wraps those pages in a SyncResult for the rest of UFO.

*Call graph*: calls 2 internal fn (__init__, _fetch_collection); 5 external calls (__init__, __init__, timeout, now, timedelta).


##### `YcSource._fetch_collection`  (lines 157–213)

```
async def _fetch_collection(self, config: YcSourceConfig, auth: SourceAuth) -> tuple[Page, ...]
```

**Purpose**: This fetches one configured YC collection, possibly across several pages of results. It is responsible for asking YC for data, checking that YC’s answer is consistent, and collecting the Page objects produced from that data.

**Data flow**: It receives a validated config and source auth information. It builds a compact JSON request for each page, runs the matching YC search command through the runner, validates the JSON envelope returned by that command, and converts the CSV results into Page objects. It stops when it has read all available results or reached the configured maximum, then returns the collected pages as a tuple.

**Call relations**: YcSource.fetch calls this only when a refresh is due. For each response from YC, this function calls YcSource._pages to choose the right CSV-to-page conversion path, then sends the final page list back up to fetch.

*Call graph*: calls 1 internal fn (_pages); called by 1 (fetch); 1 external calls (dumps).


##### `YcSource._pages`  (lines 215–218)

```
def _pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This is a small router that chooses how to turn YC CSV text into UFO pages. Guidance content and directory search results have different CSV shapes, so they need different conversion rules.

**Data flow**: It receives the collection name and the raw CSV body returned by YC. It checks whether the collection is one of the guidance libraries. If so, it sends the CSV to the guidance converter; otherwise, it sends it to the directory converter. It returns the tuple of Page objects from whichever converter was used.

**Call relations**: YcSource._fetch_collection calls this after each YC search response. This function then hands the work to either YcSource._guidance_pages or YcSource._directory_pages so the rest of the fetch flow does not need to know the CSV details.

*Call graph*: calls 2 internal fn (_directory_pages, _guidance_pages); called by 1 (_fetch_collection).


##### `YcSource._guidance_pages`  (lines 220–238)

```
def _guidance_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This converts CSV rows from YC guidance collections into searchable UFO pages. These rows include fields like link, description, body text, and categories.

**Data flow**: It receives a guidance collection name and CSV text. It reads each CSV row, validates the expected fields, joins the useful text parts into one page body, trims the body if it is too large, computes a SHA-256 digest as a change fingerprint, and creates a Page with a stable source reference, stream name, title, and body. It returns all created pages.

**Call relations**: YcSource._pages calls this when the collection is a YC guidance library. During conversion it calls YcSource._bounded so every page stays within the source size limit before being handed back to the fetch process.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 4 external calls (__init__, DictReader, sha256, StringIO).


##### `YcSource._directory_pages`  (lines 240–271)

```
def _directory_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This converts CSV rows from searchable YC directories into UFO pages. It is used for search-based collections such as companies, founders, investors, jobs, launches, and forum results.

**Data flow**: It receives a directory collection name and CSV text. For each row, it pulls out the record id and link, treats the remaining non-empty columns as named attributes, renders those attributes as readable text, trims the result if needed, computes a SHA-256 digest, and creates a Page. It returns the complete tuple of pages.

**Call relations**: YcSource._pages calls this for non-guidance YC collections. Like the guidance converter, it relies on YcSource._bounded before creating each Page so directory records cannot exceed the configured size cap.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 4 external calls (__init__, DictReader, sha256, StringIO).


##### `YcSource._bounded`  (lines 273–278)

```
def _bounded(self, body: str) -> str
```

**Purpose**: This keeps an imported YC page from becoming too large. It protects the source system from oversized records by cutting the text at a fixed byte limit and adding a clear truncation note.

**Data flow**: It receives a text body. It encodes the text as bytes, checks whether it fits within the maximum page size, and returns it unchanged if it does. If it is too large, it keeps only the largest safe prefix, decodes it back to text while ignoring any partial broken character at the cut point, appends a truncation message, and returns the shortened body.

**Call relations**: Both YcSource._guidance_pages and YcSource._directory_pages call this before making Page objects. It acts like a size gate between raw YC content and UFO’s stored source pages.

*Call graph*: called by 2 (_directory_pages, _guidance_pages).


##### `yc_index`  (lines 281–304)

```
async def yc_index(ctx: ToolContext, args: YcIndexInput) -> ToolResult
```

**Purpose**: This is the tool users call when they want UFO to index a bounded YC directory search into shared workspace memory. It does not fetch the results immediately itself; it registers the source so the normal sync system can do that work.

**Data flow**: It receives a tool context and user arguments describing the YC entity to search, the query, and the maximum number of results. It verifies that the YC extension context exists, checks that YC credentials are available, builds a YcSourceConfig from the arguments, registers that source under the shared workspace subject, and returns a short text confirmation to the user.

**Call relations**: This function is dispatched by the tool system when a user requests YC indexing. It creates the same YcSourceConfig used by YcSource.fetch later, then hands the source registration to the extension context; after that, the source-sync flow takes over.

*Call graph*: 3 external calls (__init__, __init__, __init__).

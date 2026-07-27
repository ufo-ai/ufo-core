# Collaboration, knowledge, and scheduling source connectors  `stage-14.2`

This stage is a set of read-only connectors. They sit near the start of the system’s work, where outside services are contacted and their data is turned into a common shape for later syncing, indexing, and search. Each connector is like an adapter plug for a different workplace tool.

Airtable reads bases, tables, and records, flattening Airtable’s nested structure into steady item streams. Calendly reads scheduling data such as users, event types, booked events, members, and invitees. Confluence reads Atlassian spaces, pages, blog posts, comments, groups, and audit records, and turns them into readable text. Microsoft Teams uses Microsoft Graph, Microsoft’s web doorway for app data, to collect teams, channels, chats, and messages. Notion reads users, pages, databases, blocks, and comments, preserving knowledge in a searchable form. Outlook also uses Microsoft Graph to read mail, threads, contacts, calendars, and folders, while tracking progress so future runs can resume efficiently. Slack reads users, conversations, messages, threads, and senders. Together, these files bring human communication and knowledge into the system without writing back to the original tools.

## Files in this stage

### Structured data and scheduling
Connectors that turn external operational data and scheduling objects into syncable records.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable does not offer one simple “give me everything” endpoint. Its data is arranged like a set of filing cabinets: first there are bases, then each base has tables, and each table has records. This connector walks that structure in order so the rest of the project does not need to know Airtable’s layout.

The file defines three streams: bases, tables, and records. A stream is a named type of data the sync system can ask for. For bases, the connector calls Airtable’s metadata API and returns the list. For tables, it first finds every base, then asks Airtable for the tables in each one. For records, it goes one level deeper: bases first, then tables, then record pages for each table.

Airtable returns records in pages, using an opaque offset token, meaning the connector must keep asking for the next page until there are no more. The connector also adds useful context, such as the base ID and table ID, onto tables and records. Without that, a record would arrive without enough information to tell where it came from.

This file is read-only. It does not create or change Airtable data; it only pulls data out and reshapes it into a form the shared source-sync machinery can use.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases the current credential can see. A base is like an Airtable workspace or database container.

**Data flow**: It receives an HTTP client that already knows how to make web requests. It asks Airtable for `/meta/bases`, pulls the `bases` list out of the response, and returns that list as plain dictionaries.

**Call relations**: The main pagination flow calls this whenever it needs to start from the top of Airtable’s hierarchy. It relies on the shared `records_at` helper to safely extract the list of base records from Airtable’s response.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches all tables inside one Airtable base and labels each table with the base it came from. This makes later records traceable back to their source.

**Data flow**: It receives an HTTP client and one base record. It reads the base’s `id`; if the ID is missing or not usable, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the table list, adds base information such as `base_id` and `base_name` to each table, and returns the enriched list.

**Call relations**: The pagination flow calls this after it has fetched bases. It uses `records_at` to find the tables in Airtable’s response and `with_context` to attach the base details that downstream code will need.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from one specific Airtable table, page by page. It also adds the base and table identity to every record so the record is not separated from its origin.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks that the table has a valid ID. Then it requests records from Airtable in pages of 100, following Airtable’s `offset` token when there is another page. For each non-empty page, it adds `base_id`, `table_id`, and `table_name` to the records and yields that page onward.

**Call relations**: The main pagination flow calls this after it has found a base and one of its tables. It uses the inherited cursor-page request helper for the actual repeated API calls, then uses `with_context` before handing each page back to the sync system.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the pages of data for whichever Airtable stream the sync system asks for: bases, tables, or records. This is the main dispatcher that turns a stream request into the right sequence of Airtable API reads.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. Based on the stream name, it fetches bases directly, gathers tables across all bases into pages, or walks bases and tables to yield record pages. If the stream name is unknown, it raises a skip signal instead of pretending it can sync it.

**Call relations**: This is the method the shared source framework calls when it wants Airtable data. It coordinates the helper methods `_bases`, `_tables_for_base`, and `_records_for_table`, and uses `StreamSkipped` when a requested stream is not implemented by this connector.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Cleans up and standardizes each Airtable item before the rest of the system stores or indexes it. It adds convenient fields like API URLs and normalizes record fields.

**Data flow**: It receives one raw record and the stream it belongs to. For bases, it keeps the record and adds a direct metadata API URL. For tables, it adds a URL pointing to that table under its base. For records, it exposes `created_at` from Airtable’s `createdTime` and ensures `fields` is always a dictionary. It returns the adjusted record without changing Airtable itself.

**Call relations**: After `paginate` has yielded raw pages, the source framework can call this to make individual items more useful and consistent. It does not call other project functions; it is the final shaping step for Airtable records in this file.


### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `during source sync when Calendly records are fetched and normalized`

Calendly stores scheduling data behind a web API, so this connector acts like a polite reader that knows which Calendly doors to knock on and how to turn the answers into useful pages. The first important step is finding the account's current organization, because most Calendly collections must be requested for a specific organization. If Calendly does not provide that organization, the connector skips those streams instead of guessing.

The connector reads several kinds of data. Some are simple, like the signed-in API user. Others are organization-wide collections, such as event types, groups, memberships, and scheduled events. Calendly returns these collections in pages, so the file includes shared paging logic that keeps asking for the next page token until there is no more data. For some streams, it also uses a cursor, which is a saved “last seen” value, so later syncs can ask only for newer or relevant records.

Invitees are a special case. Calendly does not expose them as one organization-wide list here, so the connector first reads scheduled events, extracts each event's ID from its URI, then asks Calendly for that event's invitees. Finally, `flatten` reshapes records to pull out common fields like name, email, title, start time, and location so downstream code can treat different Calendly objects more consistently.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like part out of a Calendly URI. It is used when the connector needs the scheduled event's short identifier in order to ask Calendly for that event's invitees.

**Data flow**: It receives any value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it trims any trailing slash, takes the text after the last slash, and returns that as the event identifier.

**Call relations**: The invitee-reading flow calls this while walking through scheduled events. Once it has the identifier, it can build the invitee API path for that specific event.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This method asks Calendly who the authenticated account is. The connector needs this because the user record contains the current organization, which is required for most other Calendly requests.

**Data flow**: It receives an HTTP client that already knows how to talk to Calendly. It requests `/users/me`, looks for the `resource` object in Calendly's response, and returns that object if it is a dictionary; otherwise it returns an empty dictionary.

**Call relations**: The organization-stream helper calls this before reading organization-scoped collections. The main pagination method also calls it directly for the API user stream, where the user record itself is the data being synced.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads a Calendly collection page by page. It hides the repeated work of passing page size, following the next-page token, and yielding each batch of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector machinery for pages whose records live under `collection` and whose next-page marker lives under `pagination.next_page_token`. It outputs one list of records at a time.

**Call relations**: Organization streams use this after adding the organization parameter. The invitee flow also uses it when reading the invitees for one scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads a Calendly collection that belongs to the current organization. It centralizes the rule that organization-scoped requests must include Calendly's organization URI.

**Data flow**: It receives an HTTP client, an API path, and optionally a cursor plus the Calendly query parameter name that should carry that cursor. It first reads the current user, extracts `current_organization`, and stops with a skipped-stream signal if there is no valid organization. It then adds the organization and optional cursor to the request parameters, reads pages, and adds organization context to every returned record batch.

**Call relations**: The main pagination method uses this for streams like event types, groups, memberships, and scheduled events. The invitee flow also uses it first to discover scheduled events before asking for their invitees. Internally it relies on the current-user lookup, the shared collection paginator, and the context helper.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads invitees for scheduled events. It exists because invitees are fetched under each individual event rather than as one simple organization-wide list.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads scheduled events for the organization. For each event, it extracts the event UUID from the event URI, skips events without a usable ID, then reads that event's invitee pages. If a cursor is present, it keeps only invitees whose `created_at` value is newer than the cursor. It yields invitee batches with extra context showing which scheduled event they came from.

**Call relations**: The main pagination method calls this for the event invitees stream. This method depends on the organization-stream reader to get scheduled events, the URI helper to find event IDs, the collection paginator to read invitees, and the context helper to attach event information.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's main routing method for reading Calendly streams. Given a requested stream, it chooses the right Calendly API path and the right incremental-sync rule.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For the API user stream, it returns the current user as a one-item batch. For organization streams, it delegates to the organization-stream helper, sometimes passing the cursor under Calendly's expected parameter name. For invitees, it delegates to the invitee-specific flow. If the stream name is unknown, it reports that the stream is not implemented.

**Call relations**: The broader source sync framework calls this when it wants records for a Calendly stream. This method then hands the work to `_current_user`, `_org_stream`, or `_invitees` depending on the stream, so the rest of the connector can keep each reading pattern separate.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Calendly records into a more convenient form for storage and search. It keeps the original fields but also lifts important values, such as names, emails, event titles, times, and locations, into predictable top-level fields.

**Data flow**: It receives one raw record and the stream description that says what kind of record it is. Depending on the stream, it copies the record and adds or rewrites friendly fields. For organization memberships, it safely reads the nested user object, pulls out name and email, and leaves the original nested user object out. For scheduled events, it turns Calendly's event name into `title`, start and end times into clearer fields, and extracts a readable location when possible. It returns the flattened dictionary.

**Call relations**: After records are fetched by the pagination flow, the source framework can call this to normalize them before they are stored or indexed. It uses the safe dictionary helper when membership records may or may not contain a proper nested user object.

*Call graph*: 1 external calls (dict_or_empty).


### Knowledge bases
Connectors that read shared knowledge spaces and convert pages, posts, comments, and audit content into searchable text.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `source sync runs`

Confluence stores page and comment bodies as storage-format XHTML, which is a markup-heavy form meant for machines, not for people reading search results. This connector solves that by fetching Confluence records through Atlassian’s API, reshaping them into the system’s common record format, and rendering page-like content as plain prose.

The connector first asks Atlassian which Confluence sites the current OAuth grant can reach. A single login grant can cover more than one site, so every stream is read once per site. For each site, it builds the right API path and reads results in pages using Confluence’s start-and-limit pagination. Some streams are incremental, meaning the system remembers the last seen timestamp. Because Confluence does not offer a true “give me only newer items” filter here, this file reads pages and then locally keeps only records newer than the saved cursor.

It also protects the sync from common permission problems. If Atlassian returns “unauthorized” or “forbidden,” the stream is marked as skipped rather than crashing the whole run. Finally, it makes Confluence bodies readable by stripping HTML tags, preserving text, and adding line breaks around block elements. Without this file, synced Confluence pages would either be missing or stored as noisy markup instead of useful human text.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the readable body markup inside a Confluence record. It looks first for the stored body and then for the viewed body, returning only a non-empty string.

**Data flow**: It receives one Confluence record as a dictionary-like object. It reads the nested body fields using path lookup, checks that the found value is real text, and returns that text or returns nothing if there is no usable body.

**Call relations**: During record shaping, ConfluenceConnector.flatten calls this helper for pages, blog posts, and comments so those records get a simple body field before later rendering turns that body into readable prose.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Confluence stream from all Confluence sites available to the current authorization grant. It is the main fetch loop for spaces, pages, comments, groups, audit entries, and similar streams.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It maps the stream name to a Confluence API path, gets the available sites, reads each site page by page, adds site context such as the cloud ID and site URL to each batch, and yields batches of records. If the API says access is refused, it turns that into a skipped stream instead of a failed sync.

**Call relations**: The sync framework calls this when it needs records for a stream. It asks ConfluenceConnector._sites which sites are reachable, delegates each site’s paged reading to ConfluenceConnector._offset_results, and uses with_context so later steps know which site each record came from.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Asks Atlassian which Confluence sites the current OAuth grant can access. This matters because one user authorization can cover multiple Confluence sites.

**Data flow**: It receives an HTTP client, sends a request to Atlassian’s accessible-resources endpoint, reads the JSON response if present, and returns it as a list. If the response is missing or not list-like, it returns an empty list through a safety helper.

**Call relations**: ConfluenceConnector.paginate calls this before reading any stream. The returned site IDs become the cloud IDs used to build site-specific Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through a Confluence collection one page at a time. It also applies local cursor filtering for incremental streams, keeping only records newer than the saved watermark.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the field that should be compared to that cursor. It requests records with start and limit values, pulls the results list out of the response, filters by the cursor when needed, yields non-empty batches, and stops when there are no records or Confluence does not provide a next-page link.

**Call relations**: ConfluenceConnector.paginate calls this for each reachable site and stream. It uses records_at to find the list of records in the API response and get_path to read nested cursor fields and pagination hints.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Confluence API records into the flatter shape expected by the rest of the sync system. It also prevents record ID collisions by adding the site cloud ID to most record IDs.

**Data flow**: It receives a raw record and its stream description. Depending on the stream, it copies useful fields into common names such as title, kind, url, body, author, created_at, updated_at, and parent_external_id. It prefixes IDs with the Confluence site ID unless the ID is itself the cursor, and it lifts nested cursor values into a flat field when needed. The result is a cleaned-up dictionary ready for storage and rendering.

**Call relations**: After pagination yields raw API records, the source framework uses this method to normalize them. It calls _body_text for page-like content and get_path whenever it needs nested values such as version timestamps or links.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Creates the human-readable title and text body that the system can recall or search. For Confluence pages, blog posts, comments, and spaces, it avoids dumping raw JSON or raw XHTML.

**Data flow**: It receives a flattened record and its stream description. For page-like streams it extracts a title and converts the stored body markup into plain text. For spaces it uses the space name or key and extracts the description text. For other streams it lets the base connector render them normally. It returns a title plus a formatted text document headed with the Confluence stream name.

**Call relations**: The source framework calls this when it needs searchable prose for a record. It relies on _StorageTextExtractor.extract to strip markup and on _str to safely treat only real strings as titles.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Sets up a small HTML parser used to collect readable text from Confluence storage-format XHTML. It starts with an empty list of text pieces.

**Data flow**: It receives no external data beyond the new object being created. It initializes the parent HTML parser with automatic character-reference conversion, so things like HTML entities become normal characters, and prepares internal storage for extracted text.

**Call relations**: _StorageTextExtractor.extract creates an instance of this parser whenever Confluence body markup needs to be converted into plain text.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts a raw Confluence markup string into plain readable text. If the input is not text, it safely returns an empty string.

**Data flow**: It receives any value. If the value is a non-empty string, it creates a parser, feeds the markup into it, asks the parser to assemble the cleaned text, and returns that result. If the value is missing or not a string, it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this for page bodies, blog post bodies, comments, and space descriptions. The method drives the parser callbacks such as handle_data, handle_starttag, and handle_endtag as the markup is read.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Keeps the actual visible text found inside the markup. This is the part a person would usually read on the Confluence page.

**Data flow**: It receives a piece of text from the HTML parser. It appends that piece to the parser’s internal list so it can be joined into the final plain-text output later.

**Call relations**: This is called automatically by the HTML parser while _StorageTextExtractor.extract feeds it markup. The collected pieces are later cleaned and joined by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when a block-style HTML tag begins. This helps paragraphs, headings, table cells, and list items stay readable after tags are removed.

**Data flow**: It receives a tag name and its attributes. It ignores the attributes completely, and if the tag is one of the known block tags, it appends a newline marker to the internal text list.

**Call relations**: The HTML parser calls this during _StorageTextExtractor.extract. Its newline markers are later cleaned up by _StorageTextExtractor._text so the final output has sensible spacing rather than one long run-on line.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-style HTML tag ends. This gives the final plain text natural breaks around paragraphs, headings, list items, and table parts.

**Data flow**: It receives a tag name. If that tag is treated as a block boundary, it appends a newline marker to the parser’s internal list; otherwise it does nothing.

**Call relations**: The HTML parser calls this while _StorageTextExtractor.extract processes the markup. Like handle_starttag, it contributes structure that _StorageTextExtractor._text later turns into clean line breaks.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Builds the final cleaned plain-text string from all collected text and newline markers. It removes extra spaces and empty lines so the result is easier to read.

**Data flow**: It joins the collected parts into one string, splits that string by newline markers, normalizes repeated whitespace inside each line, drops blank lines, and returns the final trimmed text.

**Call relations**: _StorageTextExtractor.extract calls this after the parser has finished reading the markup. It is the final cleanup step that turns parser fragments into usable prose.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. It prevents non-text values from accidentally becoming titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this when choosing titles for pages, blog posts, comments, and spaces. It keeps rendering simple and predictable when Confluence fields are missing or have unexpected types.

*Call graph*: called by 1 (render).


### Collaboration workspaces
Connectors for team workspaces that expose users, shared spaces, channels, pages, databases, blocks, and comments.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Microsoft Teams. Without it, the system would not know how to ask Microsoft for a user’s joined teams, channels, chats, or messages, and Teams content could not become recallable pages.

Microsoft Graph returns data in pages, much like a long search result split across several screens. The connector follows those pages, gathers the records, and adds useful context such as which team or chat a message came from. It starts with broad containers, like joined teams and chats, then fans out into their children: each team’s channels, each channel’s messages, and each chat’s messages.

Message streams support incremental syncing. That means the connector can receive a saved “watermark” time and only return messages whose last modified time is newer, avoiding repeated work. If one team, channel, or chat is no longer accessible, the connector skips that parent and keeps going. But if Microsoft refuses the whole stream because the user grant lacks permission, the connector reports the stream as skipped rather than treating the entire run as a crash.

For display, regular structural records use the default rendering from the base REST connector. Messages get special treatment: Microsoft stores message bodies as HTML, so this file strips the tags and produces readable text with a simple heading.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Microsoft Teams that the signed-in user has joined. This is the starting point for any team-based sync, because channels and channel messages can only be found after the connector knows which teams exist.

**Data flow**: It receives an HTTP client that already knows how to make Microsoft Graph requests. It asks `/me/joinedTeams` for pages of team records, collects every page into one list, and returns that list to the caller.

**Call relations**: When the connector needs teams directly, `MicrosoftTeamsConnector.paginate` calls this function. When it needs channels, `MicrosoftTeamsConnector._channels` calls this first so it can visit each team one by one.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches channels for each joined team. It adds team information onto each channel record so later steps know where that channel came from.

**Data flow**: It starts by calling `MicrosoftTeamsConnector._teams` to get the user’s teams. For each team with a usable ID, it asks Microsoft Graph for that team’s channels, adds context such as the team ID and team name to the returned channel records, and yields those channel pages. If one team cannot be read because access is forbidden or the team is missing, it skips that team and continues.

**Call relations**: This function sits between team discovery and message discovery. `MicrosoftTeamsConnector.paginate` calls it when syncing the channels stream, and `MicrosoftTeamsConnector._channel_messages` calls it when it needs channels before fetching messages. It uses `with_context` to attach parent team details to channel records.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every readable channel in every joined team. It can limit results to messages changed after a saved cursor, which keeps repeat syncs efficient.

**Data flow**: It receives an HTTP client and an optional cursor, which is a saved last-seen modification time. It gets channels from `MicrosoftTeamsConnector._channels`, asks Microsoft Graph for each channel’s messages, filters out old messages when a cursor is present, adds context like team ID, channel ID, and thread ID, and yields only non-empty message batches. If one channel’s messages cannot be read because the parent is forbidden or missing, it skips that channel and continues.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the requested stream is `channel_messages`. It depends on `MicrosoftTeamsConnector._channels` to find the channels first, then uses `with_context` so downstream storage can keep each message tied to its team and channel.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s Microsoft Teams chats. This gives the connector the chat IDs needed before it can read chat messages.

**Data flow**: It receives an HTTP client, asks `/me/chats` for pages of chat records, collects all pages into one list, and returns that list.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when syncing chats directly. `MicrosoftTeamsConnector._chat_messages` calls it first when it needs to walk through each chat and fetch its messages.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from the user’s Teams chats. Like channel message syncing, it can skip older messages by comparing each message’s last modified time with a cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It first gets chats from `MicrosoftTeamsConnector._chats`, then asks Microsoft Graph for messages in each valid chat. If a cursor is provided, it keeps only messages newer than that cursor, adds chat ID and thread ID context, and yields non-empty batches. If one chat cannot be read because it is forbidden or missing, it skips that chat and keeps going.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this for the `chat_messages` stream. It uses `MicrosoftTeamsConnector._chats` to find chat containers and `with_context` to mark each message with the chat it belongs to.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for Teams syncing. Given a stream name such as teams, channels, or chat messages, it chooses the right helper and yields records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields the batches that helper produces. If Microsoft returns a broad permission refusal, it turns that into `StreamSkipped`, which tells the sync run that this stream was unavailable rather than broken. If the stream name is unknown, it also reports it as skipped.

**Call relations**: The broader source syncing framework calls this function to retrieve data for one stream. From there it hands work to `MicrosoftTeamsConnector._teams`, `MicrosoftTeamsConnector._channels`, `MicrosoftTeamsConnector._channel_messages`, `MicrosoftTeamsConnector._chats`, or `MicrosoftTeamsConnector._chat_messages` depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a Microsoft Teams record into a title and readable page text. Message records get special cleanup because Microsoft stores their bodies as HTML.

**Data flow**: It receives one record and the stream it came from. For non-message streams, it delegates to the base connector’s normal rendering. For channel and chat messages, it reads the subject, pulls `body.content` from inside the record, strips HTML tags from the body, and returns a plain title plus a simple text page headed with the stream name.

**Call relations**: The sync framework uses this after records have been fetched, when it needs human-readable content for storage or search. It calls `_str` to safely read the subject, `get_path` to reach the nested body content, and `_strip_html` to make the message body readable.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a string so Teams message bodies become easier to read as plain text. If the input is not text, it returns nothing.

**Data flow**: It receives any value. If the value is a string, it replaces HTML-looking tags with spaces and trims the result; otherwise it returns `None`.

**Call relations**: `MicrosoftTeamsConnector.render` calls this while preparing channel and chat messages for display. It is a small cleanup helper used only in this file.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns an optional value into a string only when it is already text. This avoids accidentally showing non-text values as titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. It keeps rendering predictable even when Microsoft Graph omits the subject or sends a different kind of value.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `during source sync`

Notion stores information in a nested, app-specific shape: pages have properties, page bodies are made of blocks, comments have rich-text pieces, and users have profile fields. A simple raw JSON copy would be hard for a person or search system to understand. This connector acts like a translator: it talks to Notion’s web API, follows Notion’s paging rules, and then turns each record into plain prose that looks closer to what a Notion member would actually read.

The file defines the streams this connector can read: users, pages, data sources, comments, and blocks. Pages and data sources are found through Notion search, ordered by last edit time so later syncs can skip old records. Blocks are collected by walking down each page’s block tree, like opening folders inside folders, with a depth limit so a bad or huge structure cannot run forever. Comments are fetched page by page. Users come from a simpler list endpoint.

The connector also treats missing permissions carefully. If Notion says the integration is not allowed to see a stream, the run records that stream as skipped instead of treating the whole sync as broken. There is no write path here; this source only reads from Notion.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Notion. It adds the Notion API version header, which tells Notion which version of its rules this connector expects.

**Data flow**: It receives a base URL and a credential reference. It asks the shared REST connector code to build the client, then adds the required Notion-Version header. It returns the ready-to-use client.

**Call relations**: This is part of connector setup before any Notion requests are made. Later fetching functions rely on the client it returns so every Notion API call includes the correct version marker.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading each Notion stream. Given a stream name, it chooses the right fetching method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor, which is the last-seen time from a previous sync. It routes users, pages, data sources, comments, and blocks to their matching readers. It yields lists of Notion records, or raises a skip signal if the stream is unsupported or Notion refuses access.

**Call relations**: The sync framework calls this when it wants records for a stream. It hands off to _collection for users, _search for pages and data sources, _comments for comments, and _blocks for page-body blocks. If Notion returns 401 or 403, it turns that into StreamSkipped so the larger run can continue.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads pages or data sources from Notion’s search endpoint. It sorts by last edited time so the connector can pick up only records changed after the last sync point.

**Data flow**: It receives an HTTP client, the kind of Notion object to search for, and an optional cursor time. It sends repeated POST requests to /search, follows Notion’s next-cursor paging, converts the results into a list, filters out records not newer than the cursor, and yields non-empty batches.

**Call relations**: paginate uses this directly for the pages and data_sources streams. _blocks and _comments also use it first to find pages, because blocks and comments have to be fetched from each page.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the body blocks for every Notion page. It is how the system captures the text inside a page, not just the page’s properties.

**Data flow**: It receives an HTTP client and an optional cursor time. It first searches for all pages, then takes each page id and asks _block_children to walk that page’s block tree. It yields batches of block records that are new enough to sync.

**Call relations**: paginate calls this for the blocks stream. It depends on _search to discover pages and then delegates the actual recursive block reading to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the child blocks under one Notion block or page. It lets the connector capture nested page content such as headings, lists, to-do items, and paragraphs inside other blocks.

**Data flow**: It receives an HTTP client, a block id, the current nesting depth, and an optional cursor time. It stops if the depth limit is exceeded, fetches child blocks through _collection, filters blocks by last edited time when needed, yields the matching blocks, and then continues into children that are safe to descend into.

**Call relations**: _blocks starts this at each page id. This function calls _collection to fetch one level of children, then calls itself again for nested blocks. It deliberately does not descend into child pages, child databases, or AI blocks because those are separate records or special Notion content.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Notion pages. It lets the sync include discussion text, not only the main page content.

**Data flow**: It receives an HTTP client and an optional cursor time. It searches for pages, takes each page id, requests comments for that page, filters out comments older than the cursor when present, and yields non-empty comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages and _collection to page through Notion’s comments endpoint for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged list of results. It hides the repeated work of following Notion’s next cursor until all pages are read.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST connector to fetch cursor-based pages using Notion’s results and next_cursor fields. It yields each batch of records as a list.

**Call relations**: paginate uses it for users. _block_children uses it for block children, and _comments uses it for comments. It is the common reader for Notion GET endpoints that share the same paging shape.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion records into readable text for storage or recall. Without this, many records would be saved as hard-to-read structured data instead of useful prose.

**Data flow**: It receives one Notion record and its stream description. Based on the stream, it extracts a title and body text from page properties, rich-text fields, block contents, comment text, or user profile fields. It returns a short title and a formatted text document headed with the Notion stream name.

**Call relations**: The sync framework uses this after records are fetched. It calls helper functions such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text to turn Notion’s different record shapes into plain text.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only when it already is a string. It prevents accidental text like 'None' or Python object descriptions from appearing in rendered content.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: render and several text helpers call this when reading optional Notion fields such as names, titles, emails, or select labels. It keeps those helpers simple and avoids repeated type checks.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts readable text from Notion rich-text arrays. Notion stores formatted text as many small runs, and this joins their plain_text parts into one normal string.

**Data flow**: It receives a value that should be a list of rich-text pieces. If it is not a list, it returns an empty string. Otherwise it collects each piece’s plain_text field when present, joins them together, trims extra surrounding space, and returns the result.

**Call relations**: render uses this for data source titles, descriptions, and comments. _page_title, _property_text, and _block_text use it whenever they need to turn Notion formatted text into plain words.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the human title of a Notion page. Page titles are stored inside the page properties, so this helper searches those properties for the title field.

**Data flow**: It receives a page record. It looks at the properties dictionary, finds the property whose type is title, extracts its rich text, and returns the first non-empty title it finds. If the page shape is not as expected, it returns an empty string.

**Call relations**: render calls this when preparing a page record. It relies on _rich_text_text to convert Notion’s title pieces into a normal title.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into readable lines. It makes database-style fields such as status, date, people, or email show up as text.

**Data flow**: It receives a page record. It walks through the page’s properties, asks _property_text to extract a readable value for each property, and builds lines in the form 'name: value'. It returns the joined lines, or an empty string if there are no readable properties.

**Call relations**: render calls this for page bodies. It delegates the details of each property type to _property_text so page rendering does not need to know every Notion field shape.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This extracts a readable value from one Notion page property. It understands common property types such as title, rich text, select, people, date, number, URL, email, phone number, and checkbox.

**Data flow**: It receives one property dictionary. It checks the property’s type, reads the matching value field, converts supported shapes into text, and returns an empty string for unsupported or missing values.

**Call relations**: _properties_text calls this for each page property. This helper calls _rich_text_text for formatted text fields and _str for optional string fields such as names or dates.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from one Notion block. Blocks are the pieces that make up a page body, such as paragraphs, headings, lists, code, child-page links, and to-do items.

**Data flow**: It receives a block record. It finds the block’s type-specific content, returns child page or child database titles when relevant, extracts rich text for normal text blocks, and adds a checked or unchecked marker for to-do blocks. It returns an empty string when no readable text is available.

**Call relations**: render calls this for records in the blocks stream. It uses _rich_text_text for ordinary block text and _str for child page or database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This creates readable text for a Notion user record. It includes the person’s name and email address when available.

**Data flow**: It receives a user record. It reads the top-level name and, for person users, the nested email field. It returns the available parts joined on separate lines.

**Call relations**: render calls this for the users stream. It uses _str so missing or non-string name and email fields simply disappear instead of producing noisy output.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Messaging and mailbox streams
Connectors that read human communication streams from mailbox, calendar, chat, thread, and conversation APIs.

### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `source sync run`

Outlook does not hand over a mailbox in one simple response. Microsoft Graph returns data in pages, and for many objects it also gives a special “delta” link, which is like a bookmark saying, “next time, start here and I will only tell you what changed.” This file wraps that behavior for the rest of the system.

The connector defines several streams: contacts, messages, conversations, events, and mail folders. For messages and contacts, Outlook stores items inside folders, so the connector keeps a separate bookmark for each folder and stores those bookmarks as JSON. For calendar events, it asks for a moving time window around today, so old and future events can be tracked. For conversations, Outlook does not provide a dedicated synced thread object here, so the connector reads messages and groups them by conversation ID to make one record per email thread.

A central paging method chooses the right helper for each stream. Lower-level helpers talk to Graph’s delta endpoints, separate normal records from deleted records, and pass along the next bookmark. If Microsoft refuses access with a 401 or 403 status, the stream is marked as skipped rather than failed, because that usually means the user did not grant the needed mail or calendar permission.

#### Function details

##### `_strip_html`  (lines 33–36)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from text so an event description can be shown as readable plain text. It is used when Outlook gives calendar body content as HTML instead of ordinary text.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces anything that looks like an HTML tag with spaces, trims the result, and returns the cleaned text.

**Call relations**: During record cleanup, OutlookConnector.flatten uses this helper for event descriptions. That lets the flattened event record contain a plain description instead of raw HTML markup.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 39–47)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact. This gives the system a simple top-level email field even though Microsoft stores contact emails in a nested list.

**Data flow**: It receives a contact record, looks for its emailAddresses list, then checks each address entry for the nested emailAddress.address value. The first non-empty string it finds is returned; if none is found, it returns nothing.

**Call relations**: OutlookConnector.flatten calls this when preparing contact records. It relies on get_path to safely read a nested value without assuming every intermediate field exists.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 50–60)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from a contact record. It prefers the mobile phone, then falls back to the first business phone number.

**Data flow**: It receives a contact record. It first checks mobilePhone and returns it if it is a non-empty string. If not, it scans businessPhones and returns the first non-empty string there; otherwise it returns nothing.

**Call relations**: OutlookConnector.flatten calls this while turning a Microsoft contact into the project’s simpler contact shape. It keeps the selection rule in one small place instead of spreading it through the flattening code.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate`  (lines 110–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Acts as the traffic director for Outlook syncing. Given a stream name, it chooses the correct paging method and yields pages of records or change-feed pages back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark from a previous run. It dispatches to the matching helper for conversations, messages, contacts, events, or mail folders, then yields whatever pages that helper produces. If Microsoft replies that access is forbidden or unauthorized, it turns that into a skipped stream message; if the stream is unknown, it skips it as unimplemented.

**Call relations**: The sync framework calls this when it wants records for one Outlook stream. This method then hands the work to the stream-specific helpers, while converting permission refusals into StreamSkipped so one missing Microsoft Graph permission does not look like a broken connector.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages).


##### `OutlookConnector._conversation_pages`  (lines 145–183)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds email conversation records by reading messages and grouping them into threads. This is needed because conversations here are derived from messages rather than synced from a separate Outlook conversation feed.

**Data flow**: It receives an HTTP client and an optional last-seen time cursor. It asks Microsoft Graph for messages ordered by modification time, optionally only after the cursor. As messages arrive, it groups them by conversationId, keeps the earliest creation time and latest update time for each thread, and finally yields a list of conversation records.

**Call relations**: OutlookConnector.paginate calls this for the conversations stream. It uses the connector’s general OData page reader to fetch messages, then returns ready-made conversation records directly rather than using the delta-page helper.

*Call graph*: called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 185–222)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta endpoint and converts it into the project’s standard page format. Delta endpoints are Microsoft’s way of saying what is new, changed, or deleted since a saved bookmark.

**Data flow**: It receives an HTTP client, an initial Graph path, an optional cursor link, and optional query parameters. It follows the cursor if one exists, otherwise starts at the initial path. For each response, it separates live records from deleted item IDs, finds the next page link or final delta link, and yields a StreamPage containing records, deletions, and the next cursor. It keeps following next-page links until Microsoft says the delta round is complete.

**Call relations**: This is the shared engine underneath mail folder, message, contact, and event syncing. The stream-specific helpers call it so they do not each have to reimplement Graph’s nextLink and deltaLink behavior.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 224–244)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs message changes across all mail folders. Outlook message delta state is kept per folder, so this method keeps a separate bookmark for every folder it visits.

**Data flow**: It receives an HTTP client and a cursor string. It decodes that cursor into a folder-to-bookmark map, lists current mail folders, then walks each folder’s message delta feed. Each message record is tagged with its mail folder ID, the folder’s latest cursor is stored, and each yielded StreamPage carries the updated cursor map encoded back into JSON.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It first asks _list_mail_folders which folders exist, then delegates the actual Microsoft delta paging to _graph_delta_pages and uses the cursor encode/decode helpers to preserve progress across folders.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 246–273)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs contact changes from the default contacts area and any contact folders. Like messages, contacts can live in multiple places, so each folder gets its own saved bookmark.

**Data flow**: It receives an HTTP client and a cursor string. It decodes any saved folder cursors, builds a folder list that includes the default contacts location plus named contact folders, then reads each folder’s contact delta feed. As each page arrives, it updates that folder’s cursor and yields a StreamPage with the combined cursor map. If the default contacts endpoint is missing or unsupported, it quietly moves on.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It asks _list_contact_folders for folder IDs, uses _graph_delta_pages for the actual Graph change feed, and uses the cursor map helpers so later runs can resume each contact folder separately.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 275–286)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs calendar event changes within a broad time window around the current date. This keeps the event feed focused on relevant past and future calendar items instead of asking for an unlimited calendar history.

**Data flow**: It receives an HTTP client and an optional cursor. It calculates a start time one year in the past and an end time two years in the future, then asks the calendarView delta endpoint for changes in that range. It yields each StreamPage produced by the shared delta reader.

**Call relations**: OutlookConnector.paginate calls this for the events stream. This method adds the calendar-specific date window, then hands off to _graph_delta_pages to do the standard Microsoft delta-link paging.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 288–295)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of Outlook mail folders so message syncing can visit each folder. Without this, messages outside a single default folder could be missed.

**Data flow**: It receives an HTTP client, reads pages from the /me/mailFolders endpoint, and gathers every non-empty folder id string it finds. It returns a list of those folder IDs.

**Call relations**: _message_delta_pages calls this before syncing messages. The folder IDs it returns become the route map for visiting each folder’s message delta feed.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 297–304)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of Outlook contact folders so contact syncing can include contacts stored outside the default area.

**Data flow**: It receives an HTTP client, reads pages from the /me/contactFolders endpoint, and gathers every non-empty folder id string it finds. It returns that list of folder IDs.

**Call relations**: _contact_delta_pages calls this before syncing contacts. The returned IDs are used to build the Graph paths for each contact folder’s delta feed.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 306–336)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns Microsoft Graph records into friendlier records with common fields the rest of the system expects. It keeps the original record data but adds simple names like email, phone, snippet, start_at, and end_at.

**Data flow**: It receives one raw record and the stream it belongs to. For contacts, it adds name, email, phone, and created_at fields. For messages, it adds subject, snippet, sender address, sent time, and thread IDs. For events, it adds title, plain-text description, start and end times, and location. For other streams, it returns the record unchanged.

**Call relations**: After pages are fetched, the connector framework can call this to normalize each record. It uses _first_email, _phone, _strip_html, and safe nested lookups so Outlook’s nested JSON becomes easier for downstream code to search and display.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 339–348)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Turns a saved JSON cursor into a folder-to-delta-link map. This lets the connector remember a different sync bookmark for each Outlook folder.

**Data flow**: It receives a raw cursor string or nothing. If there is no cursor, invalid JSON, or JSON that is not an object, it returns an empty map. Otherwise it keeps only entries whose values are non-empty strings and returns a clean dictionary.

**Call relations**: _message_delta_pages and _contact_delta_pages call this at the start of folder-based syncing. The decoded map tells those methods where each folder should resume.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 351–352)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns the folder-to-delta-link map back into a JSON cursor string. This is how folder-specific progress is stored between sync runs.

**Data flow**: It receives a dictionary of folder IDs to cursor links. If the dictionary has entries, it serializes it to JSON with sorted keys; if it is empty, it returns nothing.

**Call relations**: _message_delta_pages and _contact_delta_pages call this whenever they yield a page. The encoded value becomes the next cursor carried forward by the sync system.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `during Slack source sync runs`

Slack does not hand over a whole workspace in one simple download. It gives lists in pages, uses special cursor tokens to ask for the next page, and sometimes reports errors inside a normal-looking HTTP response. This file is the adapter that understands those Slack habits.

The connector exposes five streams: users, conversations, conversation threads, messages, and message participants. Users and conversations are read as full snapshots, so if something disappears from Slack’s visible API results, the sync can mark it as gone. Messages are read channel by channel through Slack’s conversation history endpoint. Think of each channel as its own notebook: the connector keeps track of where it got to in each notebook, so a busy channel does not cause quiet channels to be skipped.

For message history, the file also reshapes Slack’s raw message data into three useful views: the message itself, any thread it belongs to, and the sender as a participant. Deleted messages are noted so the system can remove them. Permission problems are treated carefully. If the app cannot list users or channels at all, the stream is skipped rather than treated as a broken sync. If only one channel refuses message history, that channel is skipped and the rest continue.

#### Function details

##### `SlackApiError.__init__`  (lines 88–92)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: This creates a clear Slack-specific error when Slack says a request failed even though the HTTP request itself looked successful. It keeps the Slack error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives Slack’s error name and an optional needed permission. It builds a readable error message, stores the error code and permission on the exception object, and returns that exception object ready to be raised.

**Call relations**: When Slack replies with `ok=false`, `_ok_or_raise` calls this constructor. The resulting error then travels upward to code that knows whether the problem means “skip this stream or channel” or “this is a real failure.”

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate`  (lines 100–137)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main dispatcher for reading Slack streams. Given a requested stream, it chooses the right Slack-reading path and yields pages of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For user and conversation streams, it directly yields pages from the matching iterator. For message-related streams, it first builds a user lookup and a channel list, then uses a partitioned walk so each channel is read separately and safely. It outputs pages of normalized records or raises a skip if the stream is unknown.

**Call relations**: The sync framework calls this when it wants data from Slack. It hands simple streams to `iter_users` or `iter_conversations`; for message streams it calls `user_index`, reads conversations, and gives channel-reading callbacks to `PartitionWalk` so the shared walk machinery can control progress.

*Call graph*: calls 4 internal fn (__init__, iter_conversations, iter_users, user_index); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 120–122)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: This small inner helper lists the Slack channels that should be walked for message history. It exists so the partition walker can ask, one by one, which channel partitions are available.

**Data flow**: It reads the already-built in-memory channel map. It yields each channel ID as a separate unit of work and does not return a final value.

**Call relations**: It is created inside `SlackConnector.paginate` for message-related streams. `PartitionWalk` uses it as the source of channel partitions before asking `channel_pages` to fetch pages for each channel.


##### `SlackConnector.paginate.channel_pages`  (lines 124–125)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: This small inner helper connects the partition walker to the real Slack history reader for one channel. It supplies the channel details and current time bound needed for safe incremental reading.

**Data flow**: It receives a channel ID and a partition bound, looks up that channel’s conversation record, and returns the async iterator produced by `_channel_pages`. The output is a stream of walk pages for that one channel.

**Call relations**: It is built inside `SlackConnector.paginate` and passed into `PartitionWalk`. Whenever the walker is ready to read a channel slice, it calls this helper, which hands off to `_channel_pages`.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 139–155)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads all visible Slack users, page by page. It converts Slack’s user objects into the cleaner user shape expected by the rest of the system.

**Data flow**: It starts with no Slack cursor, asks `users.list` for a page, filters out malformed entries, flattens each valid user, and yields the resulting list if it is not empty. It then reads Slack’s next-page cursor and repeats until there is no next page.

**Call relations**: `SlackConnector.paginate` uses this for the `users` stream. `SlackConnector.user_index` also uses it when message syncing needs a quick lookup from Slack user ID to user details.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 157–197)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads all visible Slack conversations, including public channels, private channels, direct messages, and group messages. It reshapes each Slack channel-like object into a consistent conversation record.

**Data flow**: It sends paged requests to `conversations.list`, asking Slack for several conversation types. For each valid channel record, it copies identifiers and flags, derives the conversation type, extracts topic and purpose text, converts creation time, and yields batches of conversations until Slack has no next cursor.

**Call relations**: `SlackConnector.paginate` calls this directly for the `conversations` stream. It also calls it before message syncing to discover which non-archived channels should be walked for history.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 199–206)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: This builds a dictionary of Slack users keyed by user ID. Message conversion uses it to turn a bare Slack user ID into a useful sender name or email when possible.

**Data flow**: It reads every page from `iter_users`. For each user with a string ID, it stores the user record in a dictionary under that ID. It returns the completed lookup table.

**Call relations**: `SlackConnector.paginate` calls this before reading message-related streams. The resulting lookup is passed down to `_channel_pages` and then `_message_page`, where messages and participants are enriched with user details.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 208–248)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]]) -> AsyncIterator[WalkPage]
```

**Purpose**: This reads message history for one Slack channel within the time window chosen by the partition walker. It keeps one channel’s progress separate from every other channel’s progress.

**Data flow**: It receives the HTTP client, target stream, channel record, a before-or-after bound, and the user lookup. It repeatedly posts to Slack’s `conversations.history`, adding cursor and time-window parameters as needed. It filters valid raw messages, converts them into the requested stream’s page with `_message_page`, yields that page, and follows Slack cursors until the channel slice is done. If Slack refuses this one channel for known permission or availability reasons, it raises a partition skip.

**Call relations**: The `channel_pages` helper inside `SlackConnector.paginate` calls this for each channel selected by `PartitionWalk`. It uses `_slack_post` for the network call, `_next_cursor` for paging, and `_message_page` to turn raw history into records.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 250–292)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]]) -> WalkPage
```

**Purpose**: This turns one raw Slack history page into the particular record type being synced: threads, messages, or message participants. It also detects deleted-message markers for the messages stream.

**Data flow**: It receives a stream description, the channel record, raw Slack messages, and the user lookup. It skips Slack deletion marker messages after recording the deleted message IDs. For normal messages, it flattens the message, optionally derives a thread record, and optionally derives a participant record. It calculates the newest and oldest Slack timestamps on the page and returns a `WalkPage` containing the requested records and time span.

**Call relations**: `_channel_pages` calls this after each successful Slack history response. It relies on `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message` to build the three record views, then hands a `WalkPage` back to the partition walker.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 294–314)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This wraps top-level Slack list requests, such as listing users or conversations, and turns permission refusals into clean stream skips. That prevents a missing Slack permission from looking like a broken connector.

**Data flow**: It receives an HTTP client, an API path, and request parameters. It calls `_slack_get` and returns the decoded Slack data when successful. If Slack reports a known missing-permission style error, or HTTP status 403, it raises `StreamSkipped`; other errors are allowed to keep rising.

**Call relations**: `iter_users` and `iter_conversations` use this for their list calls. It sits between those higher-level iterators and `_slack_get`, adding Slack-specific skip behavior for enumeration failures.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 316–319)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This performs a Slack GET request and checks Slack’s own success flag. It hides the detail that Slack may return HTTP success while still saying the API call failed.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It calls the base connector’s GET method, passes the returned data through `_ok_or_raise`, and returns the data only if Slack marked it as okay.

**Call relations**: `_enumerate` uses this for top-level list endpoints. It delegates the Slack `ok` check to `_ok_or_raise` so GET and POST requests share the same error interpretation.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 321–324)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This performs a Slack POST request and checks Slack’s own success flag. It is used for Slack endpoints, such as conversation history, that expect request details in the body.

**Data flow**: It receives an HTTP client, a path, and optional JSON body data. It calls the base connector’s POST method, checks the Slack response with `_ok_or_raise`, and returns the successful data.

**Call relations**: `_channel_pages` uses this to call `conversations.history`. Like `_slack_get`, it relies on `_ok_or_raise` to convert Slack’s `ok=false` responses into Slack-specific errors.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 327–332)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This checks whether Slack’s response body says the API call succeeded. It is needed because Slack can report failure inside the JSON body instead of through the HTTP status alone.

**Data flow**: It receives a decoded Slack response dictionary. If `ok` is explicitly false, it reads the error code and optional needed permission, then raises `SlackApiError`. Otherwise, it returns the original data unchanged.

**Call relations**: Both `_slack_get` and `_slack_post` call this after network requests. When it raises `SlackApiError`, higher layers such as `_enumerate` or `_channel_pages` decide whether that error means a skipped stream, a skipped channel, or a failure.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 335–340)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: This extracts Slack’s next-page token from a response. The token is like a bookmark that tells the next request where to continue.

**Data flow**: It receives a Slack response dictionary, looks inside `response_metadata.next_cursor`, and returns the cursor only if it is a non-empty string. If no usable cursor exists, it returns `None`.

**Call relations**: `iter_users`, `iter_conversations`, and `_channel_pages` call this after each Slack page. Its result controls whether those loops fetch another page or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 343–350)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: This converts ordinary Unix timestamps into readable ISO date-time strings in UTC. It is used for Slack fields that store time as seconds since 1970.

**Data flow**: It receives any value. It rejects booleans, tries to treat the value as a number of seconds, and returns an ISO-formatted UTC timestamp if conversion works. If the input is missing or invalid, it returns `None`.

**Call relations**: `iter_conversations` uses this for conversation creation times, and `_flatten_user` uses it for user update times. It calls Python’s datetime conversion tool to do the actual timestamp conversion.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts_to_iso`  (lines 353–359)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: This converts Slack message timestamps into readable ISO date-time strings in UTC. Slack message times are string numbers like `1712345678.123456`, not normal date text.

**Data flow**: It receives a Slack timestamp string or `None`. If the value is present and can be read as a number, it converts it to an ISO UTC timestamp. If it is missing or invalid, it returns `None`.

**Call relations**: `_flatten_message` uses this for message send time. `_conversation_thread_from_message` uses it for thread creation, update, and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 362–389)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This reshapes Slack’s raw user object into the system’s simpler user record. It pulls useful profile details, normalizes email, and chooses sensible display names.

**Data flow**: It receives one Slack user dictionary. It reads the nested profile if present, trims and lowercases the email, chooses the first available display and real names, converts update time, and returns a new flat dictionary with stable user fields.

**Call relations**: `iter_users` calls this for every valid Slack member returned by `users.list`. It uses `_first_text` to pick the best name field and `_unix_to_iso` to convert Slack’s update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 392–427)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: This reshapes one raw Slack message into the system’s message record. It adds channel context, thread identity, sender information, readable time, and a short text preview.

**Data flow**: It receives a raw Slack message, its conversation record, and the user lookup. If the message or channel lacks a usable ID, it returns `None`. Otherwise it builds a stable message ID from channel and timestamp, finds the sender if possible, chooses the thread timestamp, converts send time, makes a snippet from the text, and returns the flattened message dictionary.

**Call relations**: `_message_page` calls this for each non-deletion raw message. It uses `_slack_ts_to_iso`, `_snippet`, and `_first_text` to produce clean values that later thread and participant builders can reuse.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 430–460)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: This creates a thread record when a Slack message is either the root of a thread or a reply inside one. Messages that are not part of a thread do not produce a thread record.

**Data flow**: It receives the flattened message, the original raw message, and the conversation record. It checks thread IDs and timestamps, decides whether the message belongs to a real thread, derives title, counts, privacy flags, last-message time, and parent channel, then returns a thread dictionary. If the message is not threaded or lacks key values, it returns `None`.

**Call relations**: `_message_page` calls this after flattening each message. If multiple messages point to the same thread, `_message_page` keeps the newest-looking thread record for that page.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 463–483)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: This creates a participant record for the sender of a message. It lets the system later understand who took part in a Slack message or thread.

**Data flow**: It receives a flattened message and the user lookup. It finds the sender’s user record if available, chooses an email or Slack user ID as the handle, and returns a participant dictionary linked to the message and thread. If no usable handle exists, it returns `None`.

**Call relations**: `_message_page` calls this for every flattened message. It uses `_first_text` to choose the best sender handle before adding the participant to the page for the `message_participants` stream.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 486–493)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: This translates Slack’s boolean conversation flags into one clear conversation type. It turns several Slack-specific flags into labels such as direct message, group message, private channel, or public channel.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s type flags in priority order and returns a string naming the conversation kind.

**Call relations**: `iter_conversations` calls this while reshaping each Slack conversation. The returned type becomes part of the normalized conversation record and is also reused later when messages include channel context.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 496–502)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: This safely reads a value from inside nested dictionaries. It avoids crashes when Slack omits a nested object or sends it in an unexpected shape.

**Data flow**: It receives a starting dictionary and a path of keys. It walks through the dictionary one key at a time. If any level is not a dictionary, it returns `None`; otherwise it returns the final value.

**Call relations**: `iter_conversations` uses this to read nested topic and purpose text from Slack channel records. It keeps that code simple and safe around missing Slack fields.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 505–509)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: This picks the first useful piece of text from several candidates. It is a small helper for choosing fallback names and handles.

**Data flow**: It receives any number of values. It scans them in order and returns the first string that is not empty after trimming whitespace. If none qualify, it returns `None`.

**Call relations**: `_flatten_user` uses this to choose display and real names. `_flatten_message` uses it to choose a sender handle. `_participant_for_message` uses it to choose the participant handle.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 512–516)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: This makes a short preview from a message’s text. It collapses messy whitespace and limits the result so previews stay compact.

**Data flow**: It receives text or `None`. If text is present, it splits and rejoins it with single spaces, then cuts it to the configured snippet length. It returns the shortened preview, or `None` if there is no usable text.

**Call relations**: `_flatten_message` calls this when building the message record. The snippet is later reused as a possible thread title or preview.

*Call graph*: called by 1 (_flatten_message).

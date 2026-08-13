# Collaboration, Messaging, and Knowledge Source Connectors  `stage-14.1.2`

This stage is a set of behind-the-scenes connectors that let the system bring in information from workplace tools outside Google Workspace. Think of each connector as an adapter plug: every service speaks its own language, and these files translate that into the project’s common record format so the rest of the system can sync, search, and recall it later.

The Confluence connector reads Atlassian wiki material such as spaces, pages, blog posts, comments, groups, and audit records, then turns it into readable text. The Notion connector does the same for Notion users, pages, databases, blocks, and comments. The Microsoft Teams connector uses Microsoft Graph, Microsoft’s shared web doorway for its apps, to collect teams, channels, chats, and messages. The Outlook connector also uses Microsoft Graph, but focuses on mail, contacts, calendar events, conversations, and folders; it tracks changes in pages so later runs can fetch only updates. The Slack connector reads users, channels, messages, threads, and participants. Together, they feed outside collaboration knowledge into one searchable system.

## Files in this stage

### Knowledge Repositories
Connectors that extract structured pages, databases, comments, and audit-style content from external knowledge systems for search and recall.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `during source sync when Confluence streams are read and rendered`

Confluence stores page content as XHTML, which is a tag-heavy HTML-like format meant for machines, not as plain prose. If the system saved that raw body as-is, a recalled page would be full of tags and macro markup instead of the words a person would recognize. This connector solves that by fetching Confluence records, reshaping them into the system’s standard fields, and rendering pages and comments as clean text.

The connector first asks Atlassian which Confluence sites the current authorization grant can reach. Each site has a cloud ID, and every later API request must include that ID, like using the right building address before looking for a room. For each stream, it pages through Confluence’s API using start and limit values. Some streams are incremental, meaning the connector compares each record’s timestamp with the saved cursor and only keeps records newer than the last sync.

It also protects against ID collisions. Two different Confluence sites might both have a page with ID 123, so most IDs are rewritten as cloud_id:id. If the grant cannot access a stream because of missing permissions, the stream is marked as skipped rather than failing the whole sync.

The second major piece is the storage text extractor. It reads Confluence’s XHTML, keeps human-readable text, adds line breaks around block elements, and drops tags and attributes.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the best text body inside a Confluence record. It looks for Confluence’s storage-format body first, then falls back to the view-format body.

**Data flow**: It receives one record as a dictionary-like object. It reads the nested body fields using safe path lookup, checks that the chosen value is a non-empty string, and returns that string; otherwise it returns nothing.

**Call relations**: This is a helper used by ConfluenceConnector.flatten when pages, blog posts, or comments are being reshaped. It relies on get_path so missing nested fields do not crash the sync.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches batches of records for one Confluence stream across all accessible Confluence sites. It is the main reading loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It chooses the correct Confluence API path, asks which sites are accessible, walks each site’s results page by page, adds site context such as cloud ID and site URL to each batch, and yields batches of records. If Confluence refuses access with a permission-related status, it turns that into a clean stream skip.

**Call relations**: The sync framework calls this when it needs records for a stream. paginate calls _sites to discover reachable Confluence sites, then calls _offset_results to read each site’s collection. Before yielding each page of records, it hands them through with_context so later steps know which site they came from. If a stream is not implemented or access is refused, it raises StreamSkipped so the run records a skip instead of treating it like a broken connector.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Asks Atlassian which Confluence sites the current authorization grant can access. This is needed because one user grant may cover more than one Confluence site.

**Data flow**: It receives an HTTP client. It makes a raw request to Atlassian’s accessible-resources endpoint, reads the JSON response if there is content, normalizes it into a list, and returns that list of site records.

**Call relations**: paginate calls this before reading any stream data. The returned site IDs are then used to build site-specific Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through one Confluence API collection using start and limit pagination. It also applies local cursor filtering for incremental streams, because Confluence does not provide a direct server-side since filter here.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the field that should be compared with that cursor. It repeatedly requests pages of results, pulls the records from the response, removes records that are not newer than the cursor when needed, yields any remaining records, and stops when there are no records or no next link.

**Call relations**: paginate calls this once for each accessible Confluence site and stream path. _offset_results uses records_at to pull the result list from the response and get_path to read nested fields such as cursor values and the next-page link.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Confluence API records into the flatter, more consistent shape expected by the rest of the sync system. It adds useful fields such as title, body, URL, creation time, parent ID, and site-scoped IDs.

**Data flow**: It receives one raw record and the stream definition it belongs to. Based on the stream name, it copies the original data and adds or normalizes important fields. For pages, blog posts, and comments it extracts the body text source; for spaces it picks names and API URLs; for other streams it mostly preserves the record. It then prefixes most primary keys with the cloud ID and lifts nested cursor fields into flat keys when needed. The result is a new flattened record dictionary.

**Call relations**: This is a connector hook used after records are fetched and before they are stored or rendered. It calls _body_text for Confluence body fields and get_path for nested values such as version.createdAt, links, and author IDs. Its output gives render and the storage layer a predictable record shape.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Creates a human-readable title and text body for Confluence records that should be recallable as prose. It replaces raw Confluence XHTML with cleaned text.

**Data flow**: It receives a flattened record and its stream definition. For pages, blog posts, comments, and spaces, it chooses a title, extracts readable text from the body or description, builds a heading, and returns a pair of title and rendered text. For streams that do not need special rendering, it falls back to the parent connector’s default rendering.

**Call relations**: The sync system calls this when it needs a searchable or displayable text version of a record. It uses _str to safely treat only real strings as titles, get_path to find nested descriptions, and _StorageTextExtractor.extract to turn Confluence’s XHTML-like content into plain readable text.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Sets up a small HTML parser that will collect readable text pieces from Confluence storage-format XHTML. It also enables automatic decoding of HTML character references, so entities like &amp; become normal characters.

**Data flow**: It starts with no input besides the new object being created. It initializes the base HTML parser and creates an empty list where text fragments and line breaks will be stored.

**Call relations**: _StorageTextExtractor.extract creates this parser whenever Confluence body or description text needs to be cleaned. The later parser callbacks fill the list that _text turns into the final string.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts a raw Confluence XHTML string into readable plain text. It is the public convenience method for using the extractor.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise it creates a parser, feeds the raw text into it, and returns the parser’s cleaned text output.

**Call relations**: ConfluenceConnector.render calls this for page bodies, blog post bodies, comments, and space descriptions. Internally it triggers the parser methods handle_data, handle_starttag, and handle_endtag as the HTML parser reads the input, then calls _text to assemble the final result.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Keeps the actual words found between HTML tags. This is where readable Confluence content is collected.

**Data flow**: It receives a piece of text from the parser. It appends that text to the extractor’s internal list without changing it.

**Call relations**: The HTML parser calls this automatically while extract is feeding it raw XHTML. The saved pieces are later joined and cleaned by _text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break marker when a block-like HTML tag begins. This helps paragraphs, list items, headings, and table cells stay separated in the final plain text.

**Data flow**: It receives a tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the internal list; otherwise it ignores the tag and all attributes.

**Call relations**: The HTML parser calls this during _StorageTextExtractor.extract. Its newline markers are later normalized by _text so the output reads like paragraphs instead of one long run-on line.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break marker when a block-like HTML tag ends. This gives the cleaned text natural spacing after paragraphs, headings, list items, and similar blocks.

**Data flow**: It receives a tag name. If the tag is one of the known block tags, it appends a newline marker to the internal list; otherwise it does nothing.

**Call relations**: The HTML parser calls this while extract processes raw Confluence XHTML. Together with handle_starttag and handle_data, it provides the raw pieces that _text cleans into final prose.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Turns the collected text fragments and newline markers into the final clean plain-text result. It removes extra whitespace while preserving meaningful line breaks.

**Data flow**: It reads the extractor’s internal list, joins all pieces together, splits them on newline markers, collapses repeated spaces inside each line, removes empty lines, and returns the cleaned string.

**Call relations**: _StorageTextExtractor.extract calls this after the parser has finished reading the raw XHTML. It is the final step that makes the extracted content suitable for recall and search.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. This prevents non-string values from accidentally becoming titles.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render uses this when choosing titles from record fields. It keeps title selection simple and avoids surprising output from numbers, dictionaries, or missing values.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `sync run and record rendering`

Notion stores information in a nested, app-specific shape rather than as simple text. A page title may live inside page properties, body text lives inside blocks, comments have rich-text fragments, and users have profile fields. This connector is the translator between that world and this system’s simpler “records with readable text” world.

The file defines the Notion streams the system can sync: users, pages, data sources, comments, and blocks. It then provides a NotionConnector that knows which Notion API endpoint to call for each stream. Pages and data sources are found through Notion search. Blocks are found by walking through each page’s block tree, like opening folders inside folders, with a safety limit so it cannot recurse forever. Comments are fetched per page. Users come from the users endpoint.

It also adds the required Notion API version header to every request. If Notion says the integration is not allowed to read something, the connector reports that stream as skipped instead of crashing the whole sync.

The other important job here is rendering. Raw Notion JSON is hard for a person to read, so this file extracts titles, property values, rich text, checklist marks, comment bodies, and user names into clear prose.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Notion and adds the Notion API version header that Notion requires. Without that header, Notion may reject requests or interpret them using the wrong API behavior.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to build the basic client, then adds the Notion-Version header. It returns the prepared client, ready to make authenticated Notion API calls.

**Call relations**: This is part of the connector setup before any stream is read. The broader REST connector calls it when preparing network access, and the client it returns is later passed into pagination and helper methods.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Notion stream. Given a requested stream, it chooses the right Notion-reading method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the saved point from a previous sync. It checks the stream name, calls the matching helper, and passes along each batch it receives. If Notion returns a permission-style error, it turns that into a skipped stream instead of a failed sync.

**Call relations**: The sync framework calls this when it wants records from Notion. It hands users to _collection, pages and data sources to _search, comments to _comments, and blocks to _blocks. If the stream is unknown or access is refused, it raises StreamSkipped so the run can continue cleanly.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Notion for pages or data sources and returns them in batches. It sorts by last edit time so the connector can resume from a previous sync point.

**Data flow**: It receives the HTTP client, the kind of Notion object to search for, and an optional cursor. It sends POST requests to Notion’s /search endpoint, converts the returned results into a list, filters out records that are not newer than the cursor, and yields non-empty batches. It follows Notion’s next cursor until there are no more pages.

**Call relations**: paginate uses this directly for page and data source streams. _blocks and _comments also use it to first find pages, because blocks and comments are discovered by starting from each page.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This finds the block content inside Notion pages. Blocks are the pieces of a Notion page, such as paragraphs, headings, checklist items, and code snippets.

**Data flow**: It receives the HTTP client and an optional cursor. It first reads all pages through _search, then takes each page ID and asks _block_children to walk that page’s block tree. It yields each batch of block records that comes back.

**Call relations**: paginate calls this when the sync asks for the blocks stream. This function does not fetch block children directly; it starts from pages and delegates the recursive walking to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the children of one Notion block or page and yields the readable block records it finds. It is how the connector captures page body text that is nested below the top-level page.

**Data flow**: It receives a block ID, the current nesting depth, and an optional cursor. It stops if the nesting is too deep. Otherwise it fetches that block’s children with _collection, filters out blocks that are not newer than the cursor, yields the remaining ones, then recurses into child blocks that are allowed to be explored.

**Call relations**: _blocks calls this for each page. While walking, it calls _collection to fetch each page of child blocks. It also calls itself again for nested blocks, but deliberately avoids descending into child pages, child databases, and AI blocks because those are treated separately or should not be expanded here.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers comments attached to Notion pages. Since Notion comments are requested per page, it first finds pages and then asks for comments on each one.

**Data flow**: It receives the HTTP client and an optional cursor. It uses _search to find pages, checks each page ID, calls _collection on the comments endpoint with that page as the target, filters comments older than the cursor, and yields non-empty comment batches.

**Call relations**: paginate calls this when the comments stream is requested. It depends on _search to discover which pages may have comments and on _collection to handle Notion’s paged comment responses.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged collection of results. It hides the repeated work of following Notion’s next_cursor field from page to page.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the parent REST helper to fetch pages of records from the results field, using start_cursor and next_cursor for pagination. It yields each list of records as it arrives.

**Call relations**: paginate uses this for users. _block_children uses it for block children, and _comments uses it for comments. It is the common “keep asking for the next page” tool for GET-based Notion endpoints.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a raw Notion record into a title and a readable text body. It exists because raw Notion API JSON is not what a person would naturally remember or search for.

**Data flow**: It receives one Notion record and the stream it came from. Depending on the stream, it extracts the most human-friendly fields: page titles and properties, data source title and description, block text, comment text, or user name and email. It returns a title plus a formatted text block headed with the Notion stream name.

**Call relations**: The sync or indexing layer calls this after records are fetched. It relies on small text-extraction helpers such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text to turn Notion-specific shapes into ordinary text.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely returns a value only when it is already a string. It prevents unexpected numbers, dictionaries, or missing values from being treated as readable text.

**Data flow**: It receives any value. If the value is a string, it returns that string. Otherwise it returns an empty string.

**Call relations**: render and several helper functions use this as a small safety check when pulling names, emails, titles, or property values out of Notion records.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts plain text from Notion’s rich-text format. Notion stores styled text as a list of small pieces, and this joins their readable plain_text parts together.

**Data flow**: It receives a value that may be a Notion rich-text list. If it is not a list, it returns an empty string. If it is a list, it keeps the plain_text from valid entries, joins them, trims extra space, and returns the result.

**Call relations**: render uses this directly for data source descriptions and comments. _page_title, _property_text, and _block_text also call it whenever they need to turn Notion rich text into normal text.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the title of a Notion page from its properties. In Notion, the title is not a simple top-level field; it is stored as a special property.

**Data flow**: It receives a page record. It looks inside the page’s properties, searches for the property whose type is title, extracts that property’s rich text, and returns the first non-empty title it finds. If no title is available, it returns an empty string.

**Call relations**: render calls this when preparing page records. This helper calls _rich_text_text because the title itself is stored in Notion’s rich-text list format.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into simple labeled lines, such as “Status: In progress”. It makes database-style page fields readable outside Notion.

**Data flow**: It receives a page record. It looks at the properties dictionary, asks _property_text to convert each supported property into text, skips empty results, and joins the remaining name-value lines with newlines. It returns that combined text.

**Call relations**: render calls this for pages after finding the title. It delegates the details of each property type to _property_text so this function can focus on assembling the page-level text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property into readable text, depending on what kind of property it is. It supports common page fields such as text, select, people, dates, numbers, URLs, emails, phone numbers, and checkboxes.

**Data flow**: It receives one property dictionary. It checks the property’s type, pulls the matching value field, and converts that value into plain text using the right rule for that type. Unsupported or malformed properties become an empty string.

**Call relations**: _properties_text calls this for each page property. This function uses _rich_text_text for text-like properties and _str for fields such as names, dates, and emails.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from a Notion block. Blocks are the building pieces of page bodies, so this is what turns paragraphs, headings, checklist items, and child-page links into plain text.

**Data flow**: It receives a block record. It finds the block’s type, gets the matching content object, and extracts text from it. For child pages or child databases it returns the title; for to-do items it prefixes the text with a checked or unchecked marker; otherwise it returns the rich text content.

**Call relations**: render calls this for records from the blocks stream. It uses _rich_text_text for normal block text and _str when reading titles from child-page or child-database blocks.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This builds readable text for a Notion user. It combines the person’s name and email when available.

**Data flow**: It receives a user record. It looks for the name and, if the record has a person section, the email address. It keeps only real strings and joins the available parts with a newline.

**Call relations**: render calls this for user records. It uses _str to safely ignore missing or non-string name and email fields.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Microsoft Collaboration
Microsoft Graph connectors that turn Teams collaboration data and Outlook communication artifacts into incremental sync records.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `source sync`

Microsoft Teams stores useful conversations in several nested places: a user belongs to teams, teams contain channels, channels contain messages, and the user may also have direct or group chats with their own messages. This connector walks that tree through Microsoft Graph, much like opening folders one by one until it reaches the notes inside.

The file defines the streams the sync system can ask for: teams, channels, channel messages, chats, and chat messages. For each stream, it knows which Microsoft Graph endpoint to call and what field identifies each record. Message streams also have a time-based cursor, meaning the connector can ask, “what changed since the last successful sync?” and skip older messages.

Microsoft Graph returns large results in pages, so this connector relies on the shared OData paging helper to follow Microsoft’s “next page” links. It adds parent context, such as the team or chat ID, to child records so later parts of the system know where each message came from. If access to one team, channel, or chat is denied or missing, it skips that parent and keeps going. But if the whole Teams or chats listing is refused, it marks the stream as skipped rather than crashing the sync. Finally, it renders message bodies by stripping simple HTML from Graph’s stored message content.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Teams that the signed-in Microsoft user has joined. Other parts of the connector use this as the starting point for finding channels and channel messages.

**Data flow**: It receives an asynchronous HTTP client that already knows how to talk to Microsoft Graph. It requests the user's joined teams page by page, collects all returned team records into one list, and returns that list to the caller.

**Call relations**: When the sync asks directly for the teams stream, paginate calls this function and yields the teams. When the connector needs channels, _channels calls this first so it knows which team IDs to visit next.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside each team the user belongs to. It also attaches the parent team information to each channel so the channel is not separated from where it came from.

**Data flow**: It starts by asking _teams for all joined teams. For each valid team ID, it requests that team's channels from Microsoft Graph. Each batch of channel records is enriched with the team ID and team name, then yielded onward. If one team cannot be read because it is forbidden or missing, that team is skipped and the rest continue.

**Call relations**: paginate calls this when the requested stream is channels. _channel_messages also calls it first, because channel messages can only be fetched after the connector knows which channels exist. It uses with_context to pass team details along with each channel record.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every readable channel and optionally keeps only messages newer than the saved sync cursor. This is what lets the system update Teams content without rereading every old channel message each time.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It asks _channels for channel records, extracts each channel's team ID and channel ID, then requests that channel's messages from Microsoft Graph. If a cursor is present, it filters out messages whose last modified time is not newer. Remaining messages are enriched with team, channel, and thread context, then yielded. If one channel cannot be read because it is forbidden or missing, that channel is skipped.

**Call relations**: paginate calls this when syncing the channel_messages stream. This function depends on _channels to discover where to look, and it uses with_context so later sync stages know which team and channel each message belongs to.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the user's direct and group chats from Microsoft Teams. This is the chat-side counterpart to fetching joined teams.

**Data flow**: It receives an asynchronous HTTP client, requests the user's chats from Microsoft Graph page by page, collects all chat records into one list, and returns that list.

**Call relations**: paginate calls this when the requested stream is chats. _chat_messages calls it first so it can visit each chat and retrieve the messages inside.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from the user's chats and optionally filters them to only new or changed messages. This makes chat conversations available to the sync system without requiring a full reread every time.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It asks _chats for the available chats, extracts each chat ID, then requests that chat's messages from Microsoft Graph. If a cursor is present, it keeps only messages with a later last-modified time. It adds the chat ID and thread ID to the message records before yielding them. If one chat is forbidden or missing, it skips that chat and keeps syncing the others.

**Call relations**: paginate calls this when syncing the chat_messages stream. It relies on _chats for the list of chat containers, and uses with_context to preserve where each message came from.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading any Microsoft Teams stream. Given a stream name, it chooses the right helper method and yields batches of records to the broader sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper for teams, channels, channel messages, chats, or chat messages, and yields the resulting record batches. If Microsoft Graph refuses access to a top-level stream because the grant lacks permission, it turns that into a StreamSkipped result. If the stream name is unknown, it also reports the stream as skipped.

**Call relations**: The sync framework calls paginate whenever it wants records for one of this connector's streams. paginate then hands work to _teams, _channels, _channel_messages, _chats, or _chat_messages. It creates StreamSkipped when the connector should record a clean skip instead of failing the whole run.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into a title and readable text for storage or recall. Message records get special treatment because Microsoft Graph stores their body as HTML.

**Data flow**: It receives one record and the stream it came from. For teams, channels, and chats, it falls back to the standard rendering from the base connector. For channel and chat messages, it reads the subject, extracts body.content, strips HTML tags, builds a simple heading, and returns the title plus readable message text.

**Call relations**: The sync system uses render after records have been fetched and need to become human-readable pages. This method calls _str to safely read the subject, get_path to reach the nested body content, and _strip_html to turn Graph's HTML message body into plain text.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Converts a simple HTML string into plainer text by removing tags. It exists because Microsoft Teams message bodies arrive from Graph as HTML, not as ready-to-read plain text.

**Data flow**: It receives any value. If the value is not a string, it returns None. If it is a string, it replaces HTML-like tags with spaces, trims whitespace at the edges, and returns the cleaned text.

**Call relations**: MicrosoftTeamsConnector.render calls this when rendering channel and chat messages. It is a small helper used only at the final presentation step, after records have already been fetched.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. This prevents non-text values from accidentally becoming confusing message titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: MicrosoftTeamsConnector.render calls this to read the message subject before building the rendered page title. It keeps the rendering step simple and predictable when Microsoft Graph omits a subject or sends an unexpected type.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `source sync runs`

This connector is the bridge between the project and a user’s Outlook mailbox. Without it, the system would not know how to ask Microsoft Graph for Outlook data, follow Microsoft’s pagination links, remember where the last sync stopped, or report deleted items correctly.

The main class, OutlookConnector, defines the Outlook streams the system can read. For most streams it uses Microsoft Graph “delta” endpoints. A delta endpoint is like a running checklist: the first pass returns all available items across pages, then gives back a special link that means “next time, start here.” Later runs use that link to receive only new, changed, or removed items.

Messages and contacts are a little more complex because Outlook stores them inside folders. This file keeps a separate saved delta link for each folder, packed into a JSON cursor. Conversations are not a separate Outlook object here; the connector reads messages and groups them by conversationId to create one thread-like record.

The file also reshapes raw Microsoft records into friendlier fields, such as contact email, message sender, event title, and plain-text event description. If Microsoft refuses access with a permission error, the connector marks the stream as skipped instead of crashing the whole run.

#### Function details

##### `_strip_html`  (lines 33–36)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Turns a piece of HTML text into simpler plain text by removing tags. It is used so calendar event descriptions are easier to store and read.

**Data flow**: It receives any value. If the value is not a string, it returns nothing. If it is a string, it replaces HTML tags such as <p> or <br> with spaces, trims the result, and returns the cleaned text.

**Call relations**: OutlookConnector.flatten calls this when it is preparing calendar event records. It helps turn Microsoft’s rich event body content into a plain description field.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 39–47)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on an Outlook contact. A contact can contain several email entries, so this picks the first one the system can use as a simple email field.

**Data flow**: It receives a contact record. It looks for the record’s emailAddresses list, checks each entry, and uses get_path to read the nested emailAddress.address value. It returns the first non-empty address string, or nothing if none is found.

**Call relations**: OutlookConnector.flatten calls this while simplifying contact records. It relies on get_path because Microsoft’s contact email value is nested inside smaller objects.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 50–60)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from an Outlook contact. It prefers the mobile phone number, then falls back to the first business phone number.

**Data flow**: It receives a contact record. It first checks mobilePhone. If that is a non-empty string, it returns it. Otherwise it checks businessPhones and returns the first non-empty phone string it finds, or nothing if none are available.

**Call relations**: OutlookConnector.flatten calls this when creating a simple phone field for contacts. It keeps the rest of the connector from needing to know Outlook’s several phone-number locations.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate`  (lines 110–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right reading strategy for each Outlook stream and yields pages of records. This is the main doorway the sync engine uses to read Outlook data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. Based on the stream name, it delegates to the correct helper for conversations, messages, contacts, events, or mail folders. It yields pages of data back to the sync engine. If Microsoft returns a permission refusal, it turns that into a StreamSkipped result so the run records a skip instead of a hard failure.

**Call relations**: The broader source framework calls paginate when it wants records for one Outlook stream. paginate then hands off to _conversation_pages, _message_delta_pages, _contact_delta_pages, _event_delta_pages, or _graph_delta_pages depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages).


##### `OutlookConnector._conversation_pages`  (lines 145–183)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records by reading messages and grouping them into email threads. Outlook conversations are derived here from messages rather than read as their own delta stream.

**Data flow**: It receives an HTTP client and an optional cursor. It asks Microsoft Graph for messages ordered by last modified time, optionally filtering to messages newer than the cursor. It groups messages by conversationId, keeps the earliest creation time and latest update time for each thread, and yields one list of conversation records.

**Call relations**: OutlookConnector.paginate calls this when the requested stream is conversations. It reads message pages through the connector’s OData page reader and returns thread-shaped records to the normal sync flow.

*Call graph*: called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 185–222)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta feed and turns it into the project’s standard StreamPage objects. This is the shared engine for streams where Microsoft can report additions, updates, and deletions.

**Data flow**: It receives an HTTP client, a starting Graph path, an optional cursor, and optional query parameters. It follows either the saved cursor or the initial path, reads each response, separates normal records from removed item IDs, and finds Microsoft’s nextLink or deltaLink. It yields StreamPage objects containing records, deletes, and the next cursor to save.

**Call relations**: paginate uses this directly for mail folders, and the message, contact, and event helpers use it for their own delta feeds. It creates StreamPage objects so the rest of the sync system receives data in one familiar format instead of Microsoft’s raw response shape.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 224–244)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook messages across all mail folders. It keeps a separate saved position for each folder because Microsoft’s message delta feed is folder-specific.

**Data flow**: It receives an HTTP client and an optional JSON cursor. It decodes the cursor into a folder-to-delta-link map, lists the user’s mail folders, and reads each folder’s message delta feed. For each message record it adds the folder ID, updates that folder’s saved cursor, and yields a StreamPage whose cursor contains the updated map.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It uses _list_mail_folders to discover folders, _graph_delta_pages to read each folder’s changes, and _decode_cursor_map/_encode_cursor_map to preserve progress across runs.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 246–273)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed Outlook contacts from the default contact area and from contact folders. Like messages, it tracks progress separately for each contact location.

**Data flow**: It receives an HTTP client and an optional JSON cursor. It decodes saved folder cursors, builds a list containing the default contact area plus named contact folders, and reads each contact delta feed. As pages arrive, it updates the saved cursor map and yields StreamPage objects with records, deletions, and the encoded next cursor. If the default contact delta endpoint is unavailable with certain harmless errors, it skips that default area and continues.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It uses _list_contact_folders to find contact folders, _graph_delta_pages to read Microsoft’s delta pages, and the cursor map helpers to remember each folder’s sync position.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 275–286)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads changed calendar events in a practical time window around today. This avoids asking for an unbounded calendar history and future.

**Data flow**: It receives an HTTP client and an optional cursor. It calculates a date range from one year in the past to two years in the future, then reads the calendarView delta feed with those dates unless a cursor already takes over. It yields the StreamPage objects produced by the shared delta reader.

**Call relations**: OutlookConnector.paginate calls this for the events stream. It delegates the actual Microsoft delta paging to _graph_delta_pages after supplying the calendar-specific date range.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 288–295)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Gets the IDs of the user’s Outlook mail folders. Message syncing needs this list because messages are read folder by folder.

**Data flow**: It receives an HTTP client. It requests mail folder pages from Microsoft Graph, scans each folder record for a valid id, collects those IDs, and returns them as a list.

**Call relations**: _message_delta_pages calls this before reading message deltas. The returned folder IDs decide which folder-specific message feeds will be visited.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 297–304)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Gets the IDs of the user’s Outlook contact folders. Contact syncing uses this so it can read contacts beyond the default contact area.

**Data flow**: It receives an HTTP client. It requests contact folder pages from Microsoft Graph, pulls out valid folder IDs, and returns those IDs as a list.

**Call relations**: _contact_delta_pages calls this while preparing its list of contact locations. The helper then reads a separate contact delta feed for each folder ID returned here.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 306–336)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into records with easier, more consistent fields. This makes Outlook data look more like the project’s standard contact, message, and event shapes.

**Data flow**: It receives one raw record and the stream it belongs to. For contacts, it adds simple name, email, phone, and created_at fields. For messages, it adds subject, snippet, sender address, sent time, and thread IDs. For events, it adds title, plain description, start and end times, and location. For other streams, it returns the record unchanged.

**Call relations**: The source framework uses flatten after records are fetched. It calls _first_email and _phone for contacts, _strip_html for event descriptions, and get_path when values are nested inside Microsoft’s response objects.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 339–348)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Reads a saved JSON cursor for folder-based streams and turns it back into a plain dictionary. This lets the connector remember a different delta link for each folder.

**Data flow**: It receives a raw cursor string or nothing. If there is no string, invalid JSON, or a non-dictionary value, it returns an empty dictionary. Otherwise it keeps only entries whose values are non-empty strings and returns a folder-to-cursor map.

**Call relations**: _message_delta_pages and _contact_delta_pages call this at the start of their work. It converts the stored cursor into the form those helpers need before they visit each folder.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 351–352)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns a folder-to-delta-link dictionary into a JSON cursor string for saving after a sync page. This is how folder-based streams carry their progress into the next run.

**Data flow**: It receives a dictionary of folder IDs to cursor links. If the dictionary is empty, it returns nothing. Otherwise it writes the dictionary as stable, sorted JSON text and returns that string.

**Call relations**: _message_delta_pages and _contact_delta_pages call this each time they yield a page. The encoded value becomes the next cursor that the sync engine can store and later pass back into the connector.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack Messaging
The Slack connector reads users, conversations, messages, threads, and participants into searchable collaboration records.

### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `source sync runs`

Slack does not send this project a neat “everything changed” feed. Instead, this connector has to walk through Slack’s Web API page by page, like reading a long book one chapter at a time and using a bookmark to remember where it stopped. It lists users and conversations as full snapshots, so if something disappears from Slack’s current view, the sync can treat it as deleted or no longer visible.

Messages are more careful. Slack history is read per channel, newest first. The connector uses a partitioned walk, meaning each channel gets its own progress marker. That matters because a very active channel should not cause a quiet channel to be skipped. From each raw Slack message page, the file can produce three different streams: message records, thread records, and participant records.

The file also understands Slack’s unusual error style. Slack may return an HTTP success response while saying `ok: false` inside the body. This connector turns that into a real error object. If the problem is missing permission, the sync records the stream or channel as skipped instead of crashing the whole run. The connector is read-only; it never writes back to Slack.

#### Function details

##### `SlackApiError.__init__`  (lines 89–93)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error when Slack says a request failed inside an otherwise successful-looking response. It keeps the Slack error code, and optionally the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives Slack’s error code and an optional needed permission → builds a readable error message and stores those details on the exception → returns an exception object ready to be raised.

**Call relations**: The response checker `_ok_or_raise` calls this when Slack returns `ok: false`. The rest of the connector catches this error to decide whether a missing permission should skip a stream or channel.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 101–114)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Acts as the standard entry point the wider sync framework uses to ask this connector for pages of Slack records. It simply forwards the request to the connector’s main pagination method.

**Data flow**: It receives an HTTP client, a stream description, the saved cursor, and the connector’s own Slack user id → passes those unchanged to `paginate` → returns the async stream of record pages produced there.

**Call relations**: The framework calls this method when it wants Slack data. This method immediately hands control to `SlackConnector.paginate`, which chooses the correct Slack-reading path for the requested stream.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 116–165)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses how to read each Slack stream. Users and conversations are listed directly, while messages, threads, and participants are built from channel history pages.

**Data flow**: It receives the target stream, saved cursor, HTTP client, and optional self-user id → branches by stream name → yields pages of normalized records, or raises a skip when the stream is not supported.

**Call relations**: It is called by `paginate_source`. For user and conversation streams it calls `iter_users` or `iter_conversations`; for message-derived streams it builds a user lookup, gathers channels, and gives per-channel work to `PartitionWalk` so each channel can be walked safely.

*Call graph*: calls 4 internal fn (__init__, iter_conversations, iter_users, user_index); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 141–143)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Provides the list of Slack channel ids that should be walked for message history. It is a small helper used to feed channel names into the partitioned history reader.

**Data flow**: It reads the channel dictionary collected earlier in `paginate` → yields one channel id at a time → gives the partition walker the set of channel-sized work units.

**Call relations**: This helper is created inside `SlackConnector.paginate` for message-derived streams. `PartitionWalk` uses it to know which channels need history pages.


##### `SlackConnector.paginate.channel_pages`  (lines 145–153)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects one channel id from the partition walker to the actual Slack history-reading function. It packages the channel’s stored conversation details with the current time window.

**Data flow**: It receives a channel id and a partition boundary, which says what time range to read → looks up the conversation metadata → returns the async pages produced by `_channel_pages`.

**Call relations**: This helper is created inside `SlackConnector.paginate` and handed to `PartitionWalk`. Whenever the walker needs records for a channel, this helper calls `SlackConnector._channel_pages`.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 167–183)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users, page by page, and turns each raw Slack member into the project’s simpler user shape. This is how the sync gets its workspace people list.

**Data flow**: It starts with no Slack cursor → repeatedly calls Slack’s `users.list` through `_enumerate` → flattens valid member objects with `_flatten_user` → yields batches of users until `_next_cursor` says there are no more pages.

**Call relations**: It is called directly by `paginate` for the users stream and by `user_index` when message processing needs user details. It relies on `_enumerate` for permission-aware API calls and `_next_cursor` for moving through Slack pages.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 185–225)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including public channels, private channels, group messages, and direct messages. It produces a cleaner conversation record for each Slack channel-like object.

**Data flow**: It repeatedly asks Slack’s `conversations.list` for a page → extracts useful fields such as name, type, privacy, archive status, topic, and creation time → yields batches until there is no next cursor.

**Call relations**: It is called by `paginate` for the conversations stream and again before message syncing to discover which channels have histories to read. It uses helpers such as `_conversation_type`, `_nested_value`, `_unix_to_iso`, and `_next_cursor` to clean Slack’s raw response.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 227–234)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table from Slack user id to cleaned user record. Message processing uses this to attach readable names and email addresses to senders.

**Data flow**: It calls `iter_users` to receive user pages → stores each user by id in a dictionary → returns that dictionary for later message enrichment.

**Call relations**: It is called by `paginate` before walking message-derived streams. The resulting lookup is passed into `_channel_pages` and then `_message_page`, where messages and participant records are enriched with user information.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 236–283)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one Slack conversation’s message history within the time window requested by the partition walker. If Slack refuses access to this one channel, it skips that channel without stopping the whole sync.

**Data flow**: It receives a conversation, stream type, time boundary, user lookup, and self-user id → posts to Slack’s `conversations.history` with the right cursor and time limits → turns raw message pages into `WalkPage` objects through `_message_page` → yields those pages until Slack has no next cursor.

**Call relations**: It is called through the `channel_pages` helper inside `paginate`. It calls `_slack_post` to read Slack history, `_message_page` to derive records, and `_next_cursor` to keep paging; it raises `PartitionSkipped` for channel-level permission or availability problems.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 285–333)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific stream being requested: messages, conversation threads, or message participants. It also notices Slack deletion markers for messages.

**Data flow**: It receives raw messages plus conversation and user context → ignores deleted-message markers except to record deletes, filters and flattens normal messages, derives thread and participant records where possible → returns a `WalkPage` with records, delete ids when relevant, and the newest/oldest Slack timestamps on that page.

**Call relations**: It is called by `_channel_pages` after each Slack history response. It delegates record shaping to `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message`, then wraps the result for `PartitionWalk` using `WalkPage`.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 335–355)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs top-level Slack listing calls in a way that treats missing permissions as a skipped stream rather than a broken sync. This is used for broad lists like users and conversations.

**Data flow**: It receives an API path and query parameters → calls `_slack_get` → returns Slack’s checked data, unless Slack or HTTP status says the app lacks permission, in which case it raises `StreamSkipped`.

**Call relations**: It is called by `iter_users` and `iter_conversations`. Those methods can therefore stop cleanly with a recorded skip when Slack refuses an entire list.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 357–360)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Wraps a Slack GET request and checks Slack’s own success flag. It hides the detail that Slack may report failure inside the response body.

**Data flow**: It receives an HTTP client, path, and optional query parameters → calls the inherited low-level GET method → sends the decoded response to `_ok_or_raise` → returns checked response data or raises a Slack-specific error.

**Call relations**: It is called by `_enumerate` for listing users and conversations. `_ok_or_raise` is the handoff point that turns Slack’s `ok: false` body into an exception.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 362–365)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Wraps a Slack POST request and checks Slack’s own success flag. It is used where Slack expects reads to be made with POST, such as conversation history.

**Data flow**: It receives an HTTP client, path, and optional JSON body → calls the inherited low-level POST method → checks the response with `_ok_or_raise` → returns checked response data or raises a Slack-specific error.

**Call relations**: It is called by `_channel_pages` when reading `conversations.history`. If Slack refuses a channel, the raised `SlackApiError` lets `_channel_pages` decide whether to skip that channel.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 368–373)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks Slack’s response body for success. Slack can send an HTTP 200 response while still saying the operation failed, so this function catches that hidden failure.

**Data flow**: It receives decoded Slack response data → if `ok` is false, extracts the error code and optional needed scope and raises `SlackApiError` → otherwise returns the original data unchanged.

**Call relations**: `_slack_get` and `_slack_post` both call this after network requests. When it raises, higher-level code such as `_enumerate` or `_channel_pages` decides whether the error means skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 376–381)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s “next page” token in a response. Without this, the connector would only read the first page of large workspaces or channels.

**Data flow**: It receives a Slack response dictionary → looks inside `response_metadata.next_cursor` → returns the cursor string if present and non-empty, otherwise returns nothing.

**Call relations**: `iter_users`, `iter_conversations`, and `_channel_pages` call this at the end of each page. Its answer decides whether those loops continue or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 384–391)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts ordinary Unix timestamps into ISO-formatted date strings, which are easier for the rest of the system to store and compare. A Unix timestamp is a number of seconds since 1970.

**Data flow**: It receives any value → rejects booleans and values that cannot become a number → converts valid seconds into a UTC timestamp string → returns the string or `null` when conversion is not possible.

**Call relations**: `iter_conversations` uses it for conversation creation times, and `_flatten_user` uses it for user update times. It relies on Python’s date conversion library for the actual timestamp conversion.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts_to_iso`  (lines 394–400)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack message timestamps into ISO-formatted date strings. Slack timestamps look like decimal strings, so they need a small custom conversion step.

**Data flow**: It receives a Slack timestamp string or nothing → parses the string as seconds in UTC → returns an ISO date string, or `null` if the value is missing or invalid.

**Call relations**: `_flatten_message` uses it for message send times, and `_conversation_thread_from_message` uses it for thread creation and update times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 403–430)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s detailed user object into the project’s simpler user record. It picks useful identity fields such as display name, real name, email, bot status, deletion status, and update time.

**Data flow**: It receives one raw Slack member dictionary → reads nested profile details, normalizes email casing, chooses the best available names, converts update time → returns a clean user dictionary keyed by Slack user id.

**Call relations**: `iter_users` calls this for each valid Slack member. It uses `_first_text` to choose the best name value and `_unix_to_iso` to format the update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 433–474)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into the project’s message record. It also filters out messages sent by this connector’s own Slack user so the system does not ingest its own live bot output.

**Data flow**: It receives a raw message, conversation context, user lookup, and optional self-user id → checks that required ids exist, skips the self user, finds sender details, builds ids, timestamps, snippet, text, and thread information → returns a message dictionary or `null` if the message should not be kept.

**Call relations**: `_message_page` calls this for every non-deletion raw message. It uses `_slack_ts_to_iso` for the sent time, `_snippet` for a short preview, and `_first_text` to choose the best sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 477–507)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread record when a message is either the root of a Slack thread or a reply inside one. Plain standalone messages do not become thread records.

**Data flow**: It receives an already-flattened message plus the original raw Slack message and conversation details → checks whether the message belongs to a real thread → builds a thread summary with title, counts, last message time, and parent channel → returns that thread record or `null`.

**Call relations**: `_message_page` calls this after flattening each message. It uses `_slack_ts_to_iso` to turn Slack reply timestamps into readable dates, and `_message_page` later keeps the newest version of each thread record on the page.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 510–530)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system later answer questions about who took part in a Slack message or thread.

**Data flow**: It receives a flattened message and user lookup → chooses a sender handle, preferring email when known → builds a participant record tied to the message and thread → returns the record or `null` if no usable handle exists.

**Call relations**: `_message_page` calls this for each flattened message. It uses `_first_text` to choose the best participant handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 533–540)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Labels a Slack conversation as an instant message, multi-person instant message, private channel, or public channel. This turns several Slack boolean flags into one easier field.

**Data flow**: It receives a raw Slack conversation dictionary → checks Slack’s type flags in priority order → returns a plain string describing the conversation type.

**Call relations**: `iter_conversations` calls this while cleaning each conversation record. The resulting type is later carried into message records as channel context.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 543–549)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value from inside nested dictionaries, such as a conversation topic’s text. It avoids crashes when Slack omits part of the expected structure.

**Data flow**: It receives a dictionary and a path of keys → walks through the dictionary one key at a time → returns the final value, or `null` if any step is not a dictionary.

**Call relations**: `iter_conversations` calls this to read nested topic and purpose values from Slack channel objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 552–556)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from several candidates. This is useful when Slack may provide the same human concept, like a name, in several possible fields.

**Data flow**: It receives any number of values → checks them in order → returns the first string that still has text after trimming spaces, or `null` if none qualify.

**Call relations**: `_flatten_user`, `_flatten_message`, and `_participant_for_message` use this to pick the best available display name, sender handle, or participant handle.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 559–563)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short, tidy preview of message text. It collapses extra whitespace and caps the result so long Slack messages do not become oversized preview fields.

**Data flow**: It receives message text or nothing → returns `null` for empty input → joins whitespace into single spaces and cuts the result to the configured snippet length → returns the short preview.

**Call relations**: `_flatten_message` calls this when building the message’s `snippet` field. That snippet is also reused when a message becomes a thread title or thread preview.

*Call graph*: called by 1 (_flatten_message).

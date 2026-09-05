# Workplace communication and knowledge connectors  `stage-14.1.4`

This stage is part of the system’s intake work: it connects to the tools people use every day and turns their conversations, emails, and documents into standard records the rest of the system can store, search, and reuse. Each connector is like an adapter plug for a different workplace product.

The Confluence connector reads Atlassian Cloud spaces, pages, blog posts, comments, groups, and audit entries, then converts wiki-style content into readable text. The Microsoft Teams connector uses Microsoft Graph, a web doorway into Microsoft services, to collect teams, channels, chats, and messages. The Notion connector reads users, pages, databases, comments, and page blocks, then flattens them into searchable text. The Outlook connector also uses Microsoft Graph, but for mail, contacts, calendars, and folders; it carefully follows change feeds, which are lists of updates delivered in pages. The Slack connector reads users, conversations, messages, threads, and senders. Together, these files make many separate workplace systems look like one consistent stream of source records.

## Files in this stage

### Wiki and team collaboration records
Connectors that turn Confluence knowledge spaces and Microsoft Teams collaboration surfaces into searchable source records.

### `extensions/sources/ufo_ext_sources/providers/confluence.py`

`io_transport` · `source sync`

Confluence is a wiki product, but its API does not hand back page text as simple prose. Page bodies arrive as storage-format XHTML, which is like HTML with extra Confluence tags for macros and structure. If the system stored that raw markup, search or recall would be noisy and hard to read. This connector fixes that by fetching Confluence records, shaping them into the project’s standard source-record form, and rendering page-like content as plain text.

The connector first asks Atlassian which Confluence sites the current OAuth grant can reach. One grant may cover more than one site, so every API request is scoped to a site-specific cloud ID. It then pages through each stream using Confluence’s start-and-limit pagination. For streams that can be synced incrementally, such as pages and comments, Confluence does not provide a true “give me changes since this time” option. Instead, the connector rereads pages of results and keeps only records newer than the saved cursor.

It also protects against ID clashes. Two different Confluence sites can both have a page with the same ID, so most IDs are rewritten as site ID plus record ID. Finally, the small HTML parser at the bottom strips tags from Confluence body content while keeping readable words and sensible line breaks.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the readable body field inside a Confluence record. Confluence may provide body content in different nested places, so this helper checks the preferred storage-format body first and then the viewed body.

**Data flow**: It receives one record as a dictionary-like object. It looks inside the record for body.storage.value, then body.view.value, and returns the first non-empty string it finds. If neither place contains usable text, it returns nothing.

**Call relations**: ConfluenceConnector.flatten calls this when preparing pages, blog posts, and comments. It gives flatten a single plain field to put into the normalized record before later rendering turns that field into readable text.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches batches of records for one Confluence stream, across every Confluence site the current authorization can access. It is the main reading loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It chooses the correct Confluence API path, asks for accessible sites, then reads each site’s records page by page. Each outgoing batch is tagged with site context such as cloud ID and site URL. If Confluence refuses access because the grant lacks permission, it reports the stream as skipped instead of treating the whole sync as broken.

**Call relations**: The broader source runner calls this through the RestConnector flow when it needs records for a stream. paginate asks _sites which Confluence sites are reachable, hands each site-specific path to _offset_results, wraps returned records with with_context, and yields them back to the sync system. When a stream is unknown or access is refused, it raises StreamSkipped so the run can record a clean skip.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Asks Atlassian which Confluence sites the current OAuth grant can reach. This matters because one user authorization can cover several Confluence sites.

**Data flow**: It receives the HTTP client already set up by the source framework. It calls Atlassian’s accessible-resources endpoint, reads the JSON response, and converts it into a list. If the response is empty or not shaped as a list, the helper it uses turns that into an empty list.

**Call relations**: paginate calls this at the start of a stream read. The site records it returns provide the cloud IDs that paginate needs in order to build the real Confluence API URLs.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through a Confluence collection one page at a time. It also does client-side incremental filtering when a saved cursor is available.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and optional cursor information. It repeatedly requests records using start and limit values, pulls the records out of the response’s results field, and, when a cursor is present, keeps only records whose cursor value is newer. It yields each non-empty batch and stops when there are no usable records or Confluence does not advertise a next page.

**Call relations**: paginate calls this for each reachable Confluence site and stream. _offset_results does the repetitive page-by-page API work and returns clean batches upward, while paginate adds site context and yields them to the rest of the sync.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Confluence API records into the project’s more consistent record shape. It picks out useful fields like title, body, URL, author, parent page, and timestamps.

**Data flow**: It receives one raw record and the stream description. Depending on the stream, it builds a new dictionary with standard fields added or renamed. For pages, blog posts, and comments it pulls body text with _body_text. It also builds browser URLs from the site URL and Confluence web UI link, prefixes most IDs with the cloud ID to avoid clashes between sites, and flattens nested cursor fields so the sync can compare watermarks easily.

**Call relations**: After paginate has delivered raw records, the source framework uses flatten as part of normalizing them. flatten relies on get_path for nested fields and _body_text for Confluence body content, then hands a cleaner record onward to storage and rendering. It deliberately avoids changing audit IDs when the ID is also the cursor, because doing so would break cursor comparisons.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Creates a readable title and text body for records that should be recalled as prose. This is especially important for Confluence pages, blog posts, comments, and space descriptions, whose API bodies may contain XHTML markup.

**Data flow**: It receives a normalized record and its stream description. For page-like streams, it chooses a title and runs the body through _StorageTextExtractor to strip markup and keep readable text. For spaces, it uses the name or key and extracts description text. For other streams, it falls back to the parent connector’s default rendering. It returns a title plus a text block headed with the Confluence stream name.

**Call relations**: The source framework calls render when it needs the human-readable version of a synced record. render uses _str to safely accept only real strings and get_path to read nested descriptions. For streams this file does not customize, it hands the job back to the RestConnector implementation.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Sets up a tiny HTML text extractor for one piece of Confluence body content. It prepares an empty list where text fragments and line-break markers will be collected.

**Data flow**: It receives no outside data beyond the new object being created. It initializes the base HTML parser with automatic character-reference conversion, so things like &amp; become &, and creates internal storage for extracted pieces. The result is a parser ready to receive raw XHTML.

**Call relations**: _StorageTextExtractor.extract creates an instance of this class before feeding it Confluence markup. The rest of the extractor methods then fill the prepared parts list as the parser reads the content.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts raw Confluence XHTML into plain readable text. It is the simple public entry point for the extractor.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise it creates a parser, feeds the raw markup into it, and asks the parser to assemble the final cleaned text.

**Call relations**: ConfluenceConnector.render uses this whenever it needs to turn a page body, comment body, blog post body, or space description into prose. Inside the extractor flow, feeding the parser causes the handle_data, handle_starttag, and handle_endtag methods to collect text and line breaks before _text produces the final result.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Collects actual words found between HTML tags. This is the part that keeps what a person would read.

**Data flow**: It receives a text fragment from the HTML parser. It appends that fragment to the extractor’s internal parts list. It does not return a value; it changes the parser’s collected state.

**Call relations**: This method is called by the HTML parser while _StorageTextExtractor.extract is feeding raw Confluence markup. Its collected fragments are later cleaned and joined by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag begins. This keeps paragraphs, list items, table cells, and headings from running together.

**Data flow**: It receives the tag name and its attributes from the HTML parser. If the tag is one of the known block tags, it appends a newline marker to the internal parts list. Attributes are ignored because the goal is readable text, not formatting or links.

**Call relations**: This method is triggered during _StorageTextExtractor.extract as the parser reads markup. The newline markers it adds are later normalized by _StorageTextExtractor._text so the final text has clean line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag ends. This helps separate sections of text in the final plain-text output.

**Data flow**: It receives the closing tag name from the HTML parser. If that tag is one of the block tags, it appends a newline marker to the collected parts. It returns nothing and only updates internal parser state.

**Call relations**: This method runs as part of the parser callbacks started by _StorageTextExtractor.extract. Together with handle_starttag and handle_data, it builds the raw pieces that _StorageTextExtractor._text later turns into tidy prose.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Turns the collected parser pieces into the final clean text. It removes extra whitespace while preserving meaningful line breaks.

**Data flow**: It reads the extractor’s internal list of text fragments and newline markers. It joins them, splits them into lines, compresses repeated spaces inside each line, drops empty lines, and returns the cleaned string. The extractor’s stored parts are not otherwise exposed.

**Call relations**: _StorageTextExtractor.extract calls this after the parser has finished reading the raw Confluence body. It is the last step before render receives plain text for the recallable output.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely treats a value as text only if it is actually a string. It prevents titles from accidentally becoming values like numbers, objects, or None.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this when choosing titles for pages, blog posts, comments, and spaces. It keeps render’s title-building logic simple and avoids surprising non-text output.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/microsoft_teams.py`

`io_transport` · `source sync`

Microsoft Teams keeps its data behind Microsoft Graph, Microsoft’s web API for Microsoft 365. This connector is the bridge between that API and the project’s source-sync framework. Without it, the system would not know where to ask for Teams data, how to follow Microsoft’s paged responses, or how to turn Teams messages into readable text.

The file defines several streams: joined teams, channels inside those teams, messages inside channels, chats, and messages inside chats. A stream is a named kind of data the sync runner can fetch. The connector starts from the signed-in user’s Teams account, asks Microsoft Graph for joined teams or chats, then fans out to the child items underneath them. It follows Microsoft Graph pagination, where large result sets arrive in batches instead of all at once.

Message streams support incremental syncing. That means the connector can use a saved timestamp, called a cursor or watermark, and only return messages changed after that point. This avoids re-reading old messages every run.

The connector is careful about permissions. If one team, channel, or chat cannot be read, it skips that parent and keeps going. If the whole grant lacks permission to list Teams or chats, it reports the stream as skipped instead of crashing the whole sync. It only reads data; it does not write back to Teams.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Teams that the signed-in Microsoft user has joined. This is the starting point for finding channels and channel messages.

**Data flow**: It receives an HTTP client that can talk to Microsoft Graph. It asks the `/me/joinedTeams` endpoint for teams, follows each returned page, gathers all team records into one list, and returns that list.

**Call relations**: When the connector needs teams directly, `MicrosoftTeamsConnector.paginate` calls this function. When it needs channels, `MicrosoftTeamsConnector._channels` calls it first so it knows which teams to look inside.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the channels that belong to each joined team. It adds team information to each channel so later steps know where the channel came from.

**Data flow**: It receives an HTTP client. First it gets all teams from `MicrosoftTeamsConnector._teams`. For each team with a usable ID, it asks Microsoft Graph for that team’s channels. Before yielding each batch, it uses `with_context` to attach the team ID and team name to the channel records. If one team is forbidden or missing, it skips that team and continues with the rest.

**Call relations**: This function sits between team discovery and message discovery. `MicrosoftTeamsConnector.paginate` calls it when the requested stream is channels, and `MicrosoftTeamsConnector._channel_messages` calls it so it can visit each channel and fetch its messages.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every readable channel in the user’s joined teams. It can limit results to messages changed after a saved cursor, which makes repeat syncs faster.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It gets channel batches from `MicrosoftTeamsConnector._channels`, then asks Microsoft Graph for messages in each channel. If a cursor is present, it keeps only messages whose `lastModifiedDateTime` is later than that cursor. For non-empty batches, it uses `with_context` to add the team ID, channel ID, and thread ID before yielding them. If a channel cannot be read because it is forbidden or gone, it skips that channel.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the sync runner asks for the `channel_messages` stream. This function relies on `MicrosoftTeamsConnector._channels` to supply the channels, then hands enriched message batches back to the sync framework.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s Microsoft Teams chats. These are direct or group chat threads outside the team/channel structure.

**Data flow**: It receives an HTTP client, calls Microsoft Graph’s `/me/chats` endpoint, follows all result pages, collects every chat record into a list, and returns that list.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the requested stream is chats. `MicrosoftTeamsConnector._chat_messages` calls it first so it knows which chat threads to inspect for messages.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from each readable Teams chat thread. Like channel messages, it supports incremental syncing by comparing message update times to a saved cursor.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It gets the chat list from `MicrosoftTeamsConnector._chats`, then asks Microsoft Graph for messages in each chat. If a cursor is supplied, it filters out messages that have not changed since that cursor. For remaining messages, it adds the chat ID and thread ID with `with_context` and yields the batch. If one chat is forbidden or missing, it skips that chat and keeps going.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the sync runner asks for the `chat_messages` stream. It depends on `MicrosoftTeamsConnector._chats` for the list of chat threads and then passes enriched message batches back upward.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching routine for the stream the sync runner asked for. It is the main doorway the source framework uses to read Teams data in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and delegates to the matching helper: teams, channels, channel messages, chats, or chat messages. It yields batches of records to the caller. If Microsoft Graph refuses access with an authorization-style error, it raises `StreamSkipped`, meaning the run should record a clean skip instead of treating it as a broken connector. If the stream name is unknown, it also raises `StreamSkipped`.

**Call relations**: The source-sync framework calls this method when it wants records for one stream. Inside, it calls `MicrosoftTeamsConnector._teams`, `MicrosoftTeamsConnector._channels`, `MicrosoftTeamsConnector._channel_messages`, `MicrosoftTeamsConnector._chats`, or `MicrosoftTeamsConnector._chat_messages` depending on the requested stream, and converts broad permission failures into `StreamSkipped`.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into text that is easier for people and search systems to read. It gives message records a simple title and cleans their HTML body.

**Data flow**: It receives one record and the stream it came from. For teams, channels, and chats, it uses the normal rendering behavior from the parent connector. For channel and chat messages, it reads the subject with `_str`, pulls `body.content` with `get_path`, removes HTML tags with `_strip_html`, and returns a title plus a plain-text page body headed with the stream name.

**Call relations**: The sync framework calls this after records have been fetched and need to become recallable pages. This method calls `_str` and `_strip_html` for safe text cleanup, and uses `get_path` to reach nested message content without assuming every field exists.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a value so a Teams message body becomes readable plain text. Microsoft Graph stores message bodies as HTML, which is not ideal for search or display.

**Data flow**: It receives any value. If the value is not a string, it returns `None`. If it is a string, it replaces anything that looks like an HTML tag with a space, trims extra space from the ends, and returns the cleaned text.

**Call relations**: `MicrosoftTeamsConnector.render` calls this while preparing channel and chat messages. It is a small helper used only for turning Microsoft Graph’s HTML message bodies into plain text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a possible value into a string only when it already is one. This prevents non-text values from accidentally appearing as confusing titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. It gives rendering a safe title value before the page text is assembled.

*Call graph*: called by 1 (render).


### Work documents and mailbox records
Connectors that normalize Notion workspace content and Outlook Graph change feeds for durable sync and retrieval.

### `extensions/sources/ufo_ext_sources/providers/notion.py`

`io_transport` · `source sync and record rendering`

Notion stores writing in a nested shape: pages have properties, page bodies are made of blocks, comments have rich text, and some blocks contain more blocks. A simple raw JSON copy would be hard for a person, or a recall/search system, to understand. This connector acts like a careful reader: it asks Notion for each kind of record, walks through page bodies, and extracts the words a Notion user would actually see.

The file defines the Notion streams the system can sync, such as users, pages, comments, and blocks. It talks to Notion through HTTP requests, always adding the required Notion API version header. For large collections, it follows Notion's paging cursors, which are like “next page” tokens. For pages and data sources, it sorts by edit time and filters out older records when doing an incremental sync.

Blocks and comments need extra work. To fetch blocks, the connector first finds pages, then walks each page's block tree, stopping before unsafe or separate record types like child pages and databases. To fetch comments, it looks up comments for each page. If Notion refuses access because the integration lacks permission, the stream is marked as skipped instead of failing the whole run.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Notion and adds the Notion API version header. Without this header, Notion may reject or misunderstand requests because its API behavior depends on the declared version.

**Data flow**: It receives a base URL and a credential object supplied by the wider source system. It asks the parent connector to build the basic client, then adds the Notion-Version header. It returns the prepared client, ready to make Notion API calls.

**Call relations**: This fits at the start of communication with Notion. The base connector supplies the normal authenticated client setup, and this method adds the Notion-specific requirement before pagination or fetching begins.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching method for each Notion stream and yields records in pages. It is the main traffic director for reading users, pages, data sources, comments, and blocks.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. Based on the stream name, it calls the matching helper and passes along the cursor where needed. It yields batches of records, or turns permission refusals into a clean stream skip instead of a run-breaking error.

**Call relations**: The sync system calls this when it wants records for a stream. It hands users to _collection, pages and data sources to _search, comments to _comments, and blocks to _blocks. If Notion returns a 401 or 403 refusal, it raises StreamSkipped so the larger run can record that this stream was unavailable.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Uses Notion's search endpoint to find pages or data sources, ordered by last edit time. This is how the connector supports incremental syncing even though Notion does not offer a direct “changed since” search option.

**Data flow**: It receives the HTTP client, the object type to search for, and an optional cursor that represents the last seen edit time. It sends repeated POST requests to /search, follows Notion's next_cursor value, turns the results into a safe list, and filters out records whose last_edited_time is not newer than the cursor. It yields only non-empty batches of matching records.

**Call relations**: paginate calls this directly for page and data source streams. _blocks and _comments also call it first because they need to discover pages before they can fetch page bodies or comments.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds every page and then reads the blocks that make up each page body. This matters because most visible Notion page content lives in blocks, not in the page record itself.

**Data flow**: It receives the HTTP client and an optional edit-time cursor. It searches all pages without filtering the pages by cursor, then takes each page id and asks _block_children to walk that page's block tree. It yields batches of block records that are new enough for the cursor.

**Call relations**: paginate calls this for the blocks stream. It first relies on _search to find page ids, then hands each page id to _block_children, which does the actual recursive block fetching.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through the children of a Notion block or page and yields the block records it finds. It is the part that follows nested page content, like opening folders inside folders, but with a depth limit for safety.

**Data flow**: It receives an HTTP client, a block id, the current nesting depth, and an optional cursor. If the depth is beyond the configured limit, it stops. Otherwise, it fetches child blocks, filters returned blocks by last_edited_time when a cursor is present, yields filtered batches, and then recursively visits child blocks that are allowed to be descended into.

**Call relations**: _blocks calls this for each page id. This function uses _collection to fetch each page of children from Notion, then calls itself again for nested blocks unless the block has no children or is a child page, child database, or AI block.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches comments attached to each Notion page. Comments are not found through the normal page search result, so this function fans out and asks for them page by page.

**Data flow**: It receives the HTTP client and an optional cursor based on comment creation time. It searches all pages, extracts each page id, calls the comments endpoint for that page, filters out comments that are not newer than the cursor, and yields non-empty comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages first, then uses _collection to page through comments for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a standard paginated Notion collection endpoint. It hides the repeated “ask for the next page until there are no more” pattern used by users, block children, and comments.

**Data flow**: It receives the HTTP client, an API path, and optional query parameters. It delegates to the base connector's cursor-paging helper with Notion's field names, such as results and next_cursor. It yields each page of records as a list.

**Call relations**: paginate uses this directly for users. _block_children uses it for block children, and _comments uses it for comment pages. It is the shared low-level reader for GET endpoints that use Notion's start_cursor and next_cursor paging style.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Notion API record into a title and readable text. This is important because Notion's useful words are often buried inside rich-text arrays and type-specific fields rather than sitting in one simple body field.

**Data flow**: It receives a record and the stream it came from. Depending on the stream, it extracts a page title and properties, a data source title and description, block text, comment text, or user name and email. It builds a clear heading and returns both the chosen title and the final text body.

**Call relations**: The wider source system uses this after records are fetched so they can be stored or recalled as prose. It calls _page_title, _properties_text, _rich_text_text, _block_text, _str, and _user_text to translate Notion-specific shapes into plain text.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. It prevents non-text values from accidentally becoming confusing output in rendered Notion text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: render, _block_text, _property_text, and _user_text use this as a small safety check whenever they expect a text field but the Notion API might provide something else.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: Extracts readable words from Notion's rich-text format. Notion stores formatted text as a list of small text runs, and this function joins their plain_text pieces into one normal string.

**Data flow**: It receives any value, usually expected to be a list of rich-text parts. If it is not a list, it returns an empty string. If it is a list, it keeps only dictionary items with a string plain_text field, joins those pieces together, trims extra space, and returns the result.

**Call relations**: render calls this for comments and data source fields. _page_title, _property_text, and _block_text also call it whenever they need to turn Notion rich text into ordinary text.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: Finds the visible title of a Notion page from its properties. A page can have many properties, so this looks for the one Notion marks as the title field.

**Data flow**: It receives a page record. It checks that the page has a properties dictionary, scans each property for one whose type is title, converts that rich text into plain text, and returns the first non-empty title it finds. If there is no usable title, it returns an empty string.

**Call relations**: render calls this when producing text for page records. It relies on _rich_text_text to do the final conversion from Notion's rich-text list into normal words.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: Turns a Notion page's properties into simple “name: value” lines. This makes database-style page metadata readable instead of leaving it as nested API data.

**Data flow**: It receives a page record. If the properties field is not a dictionary, it returns an empty string. Otherwise, it asks _property_text to convert each property value, keeps the ones that produce text, joins them with line breaks, and returns the combined text.

**Call relations**: render calls this for page records after finding the page title. It delegates the type-by-type details to _property_text so each Notion property kind can be converted consistently.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: Converts one Notion property into readable text when the property type is supported. It knows how to read common property kinds such as title, rich text, select, status, people, dates, numbers, links, email, phone, and checkbox.

**Data flow**: It receives a single property dictionary. It looks at the property's type, retrieves the matching value field, and converts that value into a string using the right rule for that type. Unsupported or malformed properties return an empty string.

**Call relations**: _properties_text calls this for each page property. It uses _rich_text_text for Notion rich-text values and _str for fields where a string is expected.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: Extracts the visible text from a Notion block. Blocks are the building blocks of a page body, such as paragraphs, headings, lists, to-do items, child page links, and code snippets.

**Data flow**: It receives a block record. It finds the block's type-specific content section, returns child page or child database titles when relevant, joins rich-text content for normal text blocks, and adds a checked or unchecked marker for to-do blocks. If the block has no readable content shape, it returns an empty string.

**Call relations**: render calls this for records from the blocks stream. It uses _rich_text_text for most block text and _str when a child page or database title is expected.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: Builds a small readable description of a Notion user. It includes the user's name and, when available, their email address.

**Data flow**: It receives a user record. It looks for the top-level name and the nested person.email value, keeps only values that are real strings, joins the available parts with a line break, and returns the result.

**Call relations**: render calls this for records from the users stream. It uses _str to avoid including missing or non-text user fields in the rendered output.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/outlook.py`

`io_transport` · `source sync runs`

Outlook does not hand over a whole mailbox in one simple response. Microsoft Graph returns data in pages, and for many Outlook objects it also gives a “delta link,” which is like a bookmark saying, “next time, start here and I will only tell you what changed.” This file wraps that behavior for contacts, messages, conversations, calendar events, and mail folders.

The OutlookConnector is the main piece. When a sync asks for a stream, it chooses the right route: messages and contacts are read folder by folder, events are read from a calendar window, folders are read directly, and conversations are built by reading messages and grouping them by Outlook’s conversation id. Deleted records are preserved as tombstones, so downstream storage can remove them instead of thinking they simply disappeared.

The file also keeps per-folder cursors as a small JSON map, because each mail or contact folder has its own Graph delta bookmark. On a first run it may apply a backfill window, meaning it avoids pulling mail older than the configured starting point. On later runs, Graph’s saved delta link carries the position forward. If Microsoft refuses access with a 401 or 403 error, the stream is marked as skipped rather than crashing the whole run. Finally, flattening functions reshape Outlook’s raw fields into friendlier names such as email, phone, snippet, and start_at.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: This helper formats a Python date and time into the exact UTC text format Microsoft Graph expects in filters. It is used when asking Graph for records after a certain point in time.

**Data flow**: It receives a datetime value → converts it to UTC, the shared world time standard → returns a string like 2024-01-01T12:00:00Z that can be placed into a Graph query.

**Call relations**: When conversation or message syncing needs an initial time floor, those flows call this helper before sending the request to Microsoft Graph. It does not fetch anything itself; it just prepares the timestamp for the callers.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: This helper removes simple HTML tags from text, mainly so calendar event descriptions are easier to read. If the input is not text, it returns nothing instead of guessing.

**Data flow**: It receives any value → checks whether it is a string → if so, replaces HTML tags with spaces and trims the result → returns plain text or None.

**Call relations**: The flatten step calls this when turning a raw Outlook event into a cleaner record. It supports that final reshaping stage rather than the network sync stage.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper finds the first usable email address in an Outlook contact record. Contacts can store email addresses as a list, so this picks one convenient primary value for the normalized output.

**Data flow**: It receives a contact dictionary → looks inside its emailAddresses list → reads each nested emailAddress.address field until it finds a non-empty string → returns that address, or None if none is found.

**Call relations**: The flatten step calls this while preparing contact records for the rest of the system. It uses the shared get_path helper to safely read nested dictionary fields.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper chooses a useful phone number from an Outlook contact. It prefers the mobile phone, then falls back to the first business phone.

**Data flow**: It receives a contact dictionary → checks mobilePhone first → if that is missing, scans businessPhones for the first non-empty text value → returns a phone number or None.

**Call relations**: The flatten step calls this when making contact records easier to search and display. It keeps the phone-choice rule in one small place.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s entry point for the broader source-sync framework. It passes along the stream, cursor, and optional backfill floor to the Outlook-specific pagination logic.

**Data flow**: It receives an HTTP client, a stream description, the saved cursor, the current user id, and possibly a backfill date → forwards the relevant pieces to paginate → yields whatever pages paginate produces.

**Call relations**: The source runner calls this method when it wants Outlook records. This method immediately hands the work to OutlookConnector.paginate, acting as a thin adapter between the general framework and this connector.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This method decides how to fetch each Outlook stream. It is the traffic director that sends contacts, messages, conversations, events, and folders to their specialized readers.

**Data flow**: It receives a stream name, saved cursor, HTTP client, and optional backfill date → selects the matching helper → yields pages of records and deletes from that helper. If Microsoft rejects access with 401 or 403, it turns that into a clean StreamSkipped result; if the stream is unknown, it also skips it.

**Call relations**: OutlookConnector.paginate_source calls this during a sync. It then calls the specific page producer for the requested stream, such as _message_delta_pages for messages or _event_delta_pages for events, and passes their pages back to the runner.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method builds conversation records from messages. Outlook conversations are not read from their own delta endpoint here; instead, messages are read and collapsed into one record per conversation id.

**Data flow**: It receives an HTTP client, an optional cursor, and an optional starting date → asks Microsoft Graph for messages ordered by lastModifiedDateTime → groups messages by conversationId → keeps the earliest creation time and latest update information for each conversation → yields one list of conversation records.

**Call relations**: OutlookConnector.paginate calls this for the conversations stream. It calls _graph_instant when it needs to format the initial backfill date for Graph, and it relies on the inherited OData page reader to walk through /me/messages.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the shared reader for Microsoft Graph delta feeds. A delta feed is Graph’s way of saying what records are new, changed, or deleted since a previous bookmark.

**Data flow**: It receives an initial API path, an optional saved cursor, and optional query parameters → repeatedly requests Graph pages → separates normal records from deleted ids marked with @removed → chooses the next page link or final delta link as the next cursor → yields StreamPage objects until there are no more pages.

**Call relations**: OutlookConnector.paginate uses this directly for mail folders, and the message, contact, and event helpers reuse it for their own streams. It is the common engine that turns Graph’s delta response shape into the system’s standard page shape.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This method syncs email messages from every mail folder. Because each folder has its own delta bookmark, it keeps a separate cursor for each one.

**Data flow**: It receives an HTTP client, optional cursor JSON, and optional backfill date → decodes the cursor map → lists mail folders → for each folder, opens or resumes that folder’s messages delta feed → adds mail_folder_id to each record → updates that folder’s saved cursor → yields StreamPage objects with the updated whole cursor map.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It calls _list_mail_folders to discover folders, _graph_delta_pages to read each folder’s changes, _graph_instant to format an initial date filter, and the cursor encode/decode helpers to store progress.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This method syncs contacts from the default contact area and any contact folders. Like messages, it tracks a separate delta bookmark for each folder.

**Data flow**: It receives an HTTP client and optional cursor JSON → decodes existing per-folder cursors → builds a folder list containing the default contacts plus named contact folders → reads each folder’s contacts delta feed → updates that folder’s cursor → yields StreamPage objects with records, deletes, and the combined cursor map. If the default contacts endpoint is unavailable with a 400 or 404, it quietly moves on.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It calls _list_contact_folders to discover extra folders, _graph_delta_pages to read changes, and the cursor map helpers to resume correctly next time.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This method syncs calendar events from a moving time window around today. It looks one year back and two years forward, which keeps the sync focused on relevant calendar data.

**Data flow**: It receives an HTTP client and optional cursor → builds start and end date parameters based on the current UTC time → asks the shared delta reader to read /me/calendarView/delta → yields the resulting pages unchanged.

**Call relations**: OutlookConnector.paginate calls this for the events stream. It delegates the actual Graph paging and delete detection to _graph_delta_pages.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This method asks Outlook which mail folders exist and returns their ids. Message syncing needs this list so it can read each folder’s own delta feed.

**Data flow**: It receives an HTTP client → pages through /me/mailFolders → collects every non-empty folder id → returns a list of ids.

**Call relations**: OutlookConnector._message_delta_pages calls this before reading messages. Once it has the folder ids, message syncing loops over them and passes each one to the delta reader.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This method asks Outlook which contact folders exist and returns their ids. Contact syncing uses this so contacts outside the default folder are not missed.

**Data flow**: It receives an HTTP client → pages through /me/contactFolders → collects every non-empty folder id → returns a list of ids.

**Call relations**: OutlookConnector._contact_delta_pages calls this while preparing its folder list. The returned ids are then used to build folder-specific contact delta URLs.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Outlook records into friendlier records with common field names. It keeps the original data but adds easier names such as email, phone, snippet, sent_at, and start_at.

**Data flow**: It receives one raw record and the stream it belongs to → if it is a contact, message, or event, it copies the record and adds normalized fields drawn from Outlook’s nested structure → returns the enriched dictionary. For streams without special rules, it returns the record as-is.

**Call relations**: The wider sync framework uses this after records have been fetched. It calls _first_email and _phone for contacts, _strip_html for event descriptions, and get_path to safely read nested Outlook fields.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: This helper turns the saved per-folder cursor string back into a dictionary. It is forgiving: bad, missing, or unexpected cursor data becomes an empty map rather than crashing the sync.

**Data flow**: It receives a raw cursor string or None → if present, tries to parse it as JSON → checks that it is a dictionary with non-empty string values → returns a clean folder-id-to-cursor dictionary.

**Call relations**: Message and contact syncing call this at the start of their folder loops. It gives them the last saved Graph delta link for each folder, if one exists.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: This helper turns the per-folder cursor dictionary into a JSON string that can be saved for the next run. If there are no cursors, it returns None.

**Data flow**: It receives a dictionary of folder ids to cursor links → if the dictionary has entries, serializes it to stable JSON with sorted keys → returns that string; otherwise returns None.

**Call relations**: Message and contact syncing call this after each folder page updates progress. The encoded string becomes the next cursor handed back in each StreamPage.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack workspace conversations
The Slack connector reads users, channels, messages, threads, and senders into consistent searchable records.

### `extensions/sources/ufo_ext_sources/providers/slack.py`

`io_transport` · `during Slack source sync`

This connector is the bridge between Slack and the source-sync system. Slack does not hand over one neat export file. Instead, it exposes web API endpoints, and large results arrive page by page using a cursor, like turning through a long address book with a bookmark for the next page. This file knows which Slack endpoints to call, how to keep following those bookmarks, and how to translate Slack’s raw fields into the project’s standard record shapes.

It has two broad jobs. First, it takes full snapshots of users and conversations. If a user or channel disappears from what the Slack grant can see, the sync treats that as missing and can mark the old stored record as gone. Second, it walks channel message history. Slack returns messages newest first, so the connector works channel by channel and keeps each channel’s position separately. That prevents a busy channel from causing quiet channels to be skipped.

The file is also careful about Slack’s unusual error style. Slack may return a successful HTTP response while saying `ok=false` inside the response body. Missing permission can mean “skip this stream” rather than “crash the whole run.” For messages, it filters out the connector’s own live bot user, recognizes deleted messages, and derives three related views from the same raw Slack messages: message records, thread records, and participant records.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a clear error object for Slack API failures that are reported inside the response body. It keeps Slack’s error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives a Slack error name and an optional missing-scope value. It builds a readable error message, stores the error details on the object, and returns an exception ready to be raised.

**Call relations**: When Slack says `ok=false`, `_ok_or_raise` calls this constructor. The resulting error then travels upward to code that decides whether the problem is a missing permission, a channel-specific refusal, or a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard entry point the source-sync framework uses to ask Slack for pages of records. It mostly exists to adapt the framework’s expected method name to this connector’s main pagination method.

**Data flow**: It receives an HTTP client, a stream description, a saved cursor, the connector’s own Slack user id, and an optional backfill date. It passes those inputs straight into `SlackConnector.paginate` and returns the pages that method produces.

**Call relations**: The broader sync system calls this method when it wants records from Slack. This method immediately hands the work to `SlackConnector.paginate`, which chooses the correct Slack walk for the requested stream.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses how to read each Slack stream. Users and conversations are read as simple paged lists, while message-related streams are read by walking each channel’s history separately.

**Data flow**: It takes the stream being synced plus cursor and backfill information. For users or conversations, it yields cleaned pages from the corresponding iterator. For message streams, it first builds a user lookup and a channel list, then uses `PartitionWalk` to read each channel’s messages with the right time bounds, yielding stream pages as it goes.

**Call relations**: `SlackConnector.paginate_source` calls this as the main dispatcher. It calls `iter_users`, `iter_conversations`, `user_index`, `_slack_ts`, and constructs a `PartitionWalk`; if the stream is unknown, it raises `StreamSkipped` so the run records a skip rather than pretending data was read.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies the list of channel ids that should be walked for message history. It is a small helper used by the partition walker.

**Data flow**: It reads the already-collected channel dictionary. One by one, it yields each channel id as a separate unit of work.

**Call relations**: It lives inside `SlackConnector.paginate` and feeds `PartitionWalk`. The walker uses these channel ids so each Slack conversation has its own saved progress.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects one channel id from the partition walker to the code that actually fetches that channel’s Slack history. It packages the needed context for `_channel_pages`.

**Data flow**: It receives a channel id and a time bound chosen by `PartitionWalk`. It looks up the full conversation record, then returns the async page iterator from `_channel_pages`.

**Call relations**: It is passed into `PartitionWalk` by `SlackConnector.paginate`. Each time the walker wants pages for a channel, this helper hands the request to `_channel_pages` with the HTTP client, stream, user index, and self-user id.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Slack workspace members page by page and turns them into the project’s user records. This is how the sync learns who exists in the workspace.

**Data flow**: It starts without a Slack cursor, calls the `users.list` endpoint, filters out invalid member objects, flattens each valid user with `_flatten_user`, yields non-empty pages, then follows Slack’s next cursor until there is no next page.

**Call relations**: `SlackConnector.paginate` calls this when syncing the `users` stream, and `SlackConnector.user_index` calls it to build a lookup table for message authors. It relies on `_enumerate` for permission-aware API calls, `_flatten_user` for shaping records, and `_next_cursor` for pagination.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Slack channels, private channels, group direct messages, and direct messages page by page. It turns Slack’s conversation objects into cleaner conversation records.

**Data flow**: It asks Slack’s `conversations.list` endpoint for pages, including archived conversations. For each valid conversation, it extracts names, type flags, privacy/archive details, creation time, topic, purpose, and member count, then yields each non-empty page and follows the next cursor.

**Call relations**: `SlackConnector.paginate` calls this both for the `conversations` stream and to discover channels before reading message history. It uses `_enumerate` for the API request and helper functions to classify conversations, pull nested fields, convert times, and read the next cursor.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table from Slack user id to user record. Message processing uses this to attach useful author details like email or display name.

**Data flow**: It reads all pages from `iter_users`. For each user with a string id, it stores that user record in a dictionary keyed by id, then returns the completed dictionary.

**Call relations**: `SlackConnector.paginate` calls this before syncing message-derived streams. Later, `_flatten_message` and `_participant_for_message` receive the lookup so message records can name their senders more clearly.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Fetches one channel’s message history from Slack within the time window chosen by the partition walker. If a single channel cannot be read, it can skip that channel without stopping the whole Slack sync.

**Data flow**: It receives a conversation, a stream, a time bound, the user lookup, and the connector’s own Slack user id. It builds `conversations.history` request parameters, follows Slack cursors, catches channel-specific permission errors, filters raw messages to those with timestamps, and yields `WalkPage` objects created by `_message_page`.

**Call relations**: `SlackConnector.paginate.channel_pages` calls this for each channel chosen by `PartitionWalk`. It calls `_slack_post` to talk to Slack, `_message_page` to turn raw history into records, `_next_cursor` to keep paging, and raises `PartitionSkipped` when Slack refuses one channel for an expected reason.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific record type being synced: messages, conversation threads, or message participants. It also reports the timestamp range of that page so the channel walker can remember progress.

**Data flow**: It receives raw Slack messages plus conversation and user context. It notes deleted-message events, skips the connector’s own bot user through `_flatten_message`, derives possible thread records and participant records, computes the newest and oldest Slack timestamps on the page, and returns a `WalkPage` containing the records for the requested stream.

**Call relations**: `_channel_pages` calls this after fetching a page from Slack. It uses `_flatten_message`, `_conversation_thread_from_message`, and `_participant_for_message`, then hands a `WalkPage` back to `PartitionWalk` so the wider sync can store records and advance the channel cursor.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes a top-level Slack list request and turns missing-permission cases into a clean stream skip. This prevents an under-scoped Slack grant from being treated as a broken connector.

**Data flow**: It receives an endpoint path and query parameters. It calls `_slack_get`; if Slack reports a known permission refusal, or the HTTP response is a 403, it raises `StreamSkipped`; otherwise it returns the Slack data or lets unexpected errors rise.

**Call relations**: `iter_users` and `iter_conversations` use this for their list endpoints. It sits between low-level HTTP calls and the stream iterators, translating Slack permission failures into the source-sync framework’s skip signal.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a Slack GET request and verifies Slack’s own success flag. This hides Slack’s `ok=false` response pattern from the rest of the connector.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It calls the base connector’s GET method, passes the returned JSON-like data to `_ok_or_raise`, and returns the checked data.

**Call relations**: `_enumerate` calls this for Slack list endpoints. It delegates the Slack-specific success check to `_ok_or_raise`.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a Slack POST request and verifies Slack’s own success flag. It is used for Slack endpoints, such as channel history, that expect data in the request body.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It calls the base connector’s POST method, checks the returned data with `_ok_or_raise`, and returns the successful data.

**Call relations**: `_channel_pages` calls this when reading `conversations.history`. Like `_slack_get`, it hands Slack’s `ok=false` convention to `_ok_or_raise`.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether Slack’s response body says the call succeeded. Slack can return HTTP 200 while still reporting failure, so this function catches that hidden failure.

**Data flow**: It receives a response dictionary. If `ok` is exactly false, it extracts Slack’s error code and optional missing-scope detail, raises `SlackApiError`, and otherwise returns the original data unchanged.

**Call relations**: `_slack_get` and `_slack_post` call this after network requests. When it creates `SlackApiError`, higher-level code such as `_enumerate` or `_channel_pages` decides whether that error means skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s bookmark for the next page of results. Without it, the connector would only read the first page of large workspaces or channels.

**Data flow**: It receives a Slack response dictionary, looks inside `response_metadata.next_cursor`, and returns the cursor only if it is a non-empty string. If no usable cursor exists, it returns nothing.

**Call relations**: `iter_users`, `iter_conversations`, and `_channel_pages` call this after each Slack page. Its result controls whether those loops keep asking Slack for more data or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack’s ordinary Unix timestamp values into ISO date strings, which are easier for the rest of the system to store and compare. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives any value. If the value looks like a number and is not a boolean, it converts it to a UTC datetime string; if the value is missing or invalid, it returns nothing.

**Call relations**: `iter_conversations` uses this for conversation creation times, and `_flatten_user` uses it for user update times. It relies on Python’s datetime conversion to produce the final string.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a normal datetime into Slack’s special message timestamp string format. This is used as a time floor when backfilling old message history.

**Data flow**: It receives an optional datetime. If there is no datetime or it is before the Unix epoch, it returns nothing; otherwise it formats the timestamp with fixed width and microsecond precision so string comparisons sort correctly.

**Call relations**: `SlackConnector.paginate` calls this before creating `PartitionWalk` for message streams. The formatted value becomes the floor that stops backfill from walking farther back than requested.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack’s message timestamp string into a standard UTC date string. This makes message and thread times readable and consistent with other sources.

**Data flow**: It receives a Slack timestamp string or nothing. If the value is present and numeric, it converts it to an ISO datetime string; if not, it returns nothing.

**Call relations**: `_flatten_message` uses this for message send times, and `_conversation_thread_from_message` uses it for thread activity times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s nested user object into a simpler user record for storage. It chooses the best available display name and normalizes email text.

**Data flow**: It receives one raw Slack member dictionary. It reads the profile section if present, trims and lowercases email, chooses display and real names with `_first_text`, converts update time with `_unix_to_iso`, and returns a flat dictionary of user fields.

**Call relations**: `iter_users` calls this for every valid Slack member. The cleaned records are yielded directly for the users stream and are also used by `user_index` to enrich message records.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into the project’s standard message record. It also filters out messages from the connector’s own live bot user so the sync does not index itself.

**Data flow**: It receives a raw message, its conversation, the user lookup, and the connector’s own Slack user id. It validates the timestamp and channel id, skips self-authored bot messages, looks up author details, builds ids for the message and thread, converts send time, creates a short snippet, and returns the message record or nothing if the message should be ignored.

**Call relations**: `_message_page` calls this for each raw Slack message that is not a deletion notice. It uses `_slack_ts_to_iso`, `_snippet`, and `_first_text`, and its output feeds both thread creation and participant creation.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread record when a Slack message is either the root of a thread or a reply inside one. Standalone messages without thread activity do not become thread records.

**Data flow**: It receives a flattened message, the original raw Slack message, and the conversation. It checks thread ids and timestamps, decides whether the message belongs to a real thread, calculates counts and latest activity when available, and returns a thread dictionary or nothing.

**Call relations**: `_message_page` calls this after `_flatten_message`. Its returned records are gathered when the current stream is `conversation_threads`.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a sender participant record for a message. This lets the system represent who took part in a message or thread, not just the message text.

**Data flow**: It receives a flattened message and the user lookup. It chooses a handle, preferably the user’s email and otherwise the Slack user id; if no handle exists, it returns nothing. Otherwise it builds a participant record linked to the message, channel, and thread.

**Call relations**: `_message_page` calls this for each usable flattened message. It uses `_first_text` to choose the handle, and its records are emitted when the current stream is `message_participants`.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Turns Slack’s many conversation flags into one plain conversation type. This gives downstream code a single label instead of several Slack-specific booleans.

**Data flow**: It receives a raw Slack conversation. It checks whether it is a direct message, multi-person direct message, private channel, or public channel, and returns the matching string.

**Call relations**: `iter_conversations` calls this while shaping each conversation record. The result becomes the `conversation_type` field used later by message and thread records.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value buried inside nested dictionaries. It is used for Slack fields like topic and purpose, which live under sub-objects.

**Data flow**: It receives a dictionary and a path of keys. It walks through the keys one at a time; if any step is not a dictionary, it returns nothing, otherwise it returns the final value.

**Call relations**: `iter_conversations` calls this to read conversation topic and purpose text without crashing when Slack omits or reshapes those nested objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first usable text value from a list of possibilities. It is a small helper for fields where Slack may provide several possible names or handles.

**Data flow**: It receives any number of values. It returns the first value that is a non-empty string after trimming spaces, or nothing if none qualify.

**Call relations**: `_flatten_user`, `_flatten_message`, and `_participant_for_message` call this when picking display names, author handles, and participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short preview of a message’s text. This gives search or listing views a compact summary without storing a separate hand-written title.

**Data flow**: It receives optional message text. If present, it collapses repeated whitespace into single spaces, cuts the result to the configured snippet length, and returns it; empty input produces nothing.

**Call relations**: `_flatten_message` calls this while building a message record. The snippet can then be reused by thread records as a title or preview.

*Call graph*: called by 1 (_flatten_message).

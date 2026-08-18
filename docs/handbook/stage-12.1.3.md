# Collaboration, messaging, and knowledge source connectors  `stage-12.1.3`

This stage is shared behind-the-scenes support for bringing outside team knowledge into the system. Its connectors act like careful librarians: they visit approved workplace tools, read what the user has access to, and translate it into plain records that the rest of the product can store, search, and recall.

The Confluence connector reads Atlassian spaces, pages, blog posts, comments, groups, and audit records, turning wiki-style content into searchable text. The Microsoft Teams connector uses Microsoft Graph, Microsoft’s standard web doorway for Microsoft 365 data, to collect teams, channels, chats, and messages. The Notion connector reads users, pages, databases, comments, and page blocks, but never changes anything in Notion. The Outlook connector also uses Microsoft Graph to read email, conversations, contacts, calendars, and folders, and remembers its last stopping point so future syncs only fetch changes. The Slack connector reads workspace users, channels, messages, threads, and senders. Together, these files feed the system’s memory without posting, editing, or deleting in the original tools.

## Files in this stage

### Confluence knowledge spaces
Reads Atlassian Confluence spaces, pages, posts, comments, groups, and audit records as searchable text.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `source sync`

Confluence pages are not stored as simple plain text. Their bodies come back from the API as storage-format XHTML, which is a kind of HTML-like markup full of tags, tables, macros, and formatting details. If the system saved that raw markup, a person searching later would see noisy code instead of the words they remember reading. This connector solves that by fetching Confluence records and reshaping them into useful recall text.

The file defines which Confluence streams can be synced, such as pages, blog posts, comments, spaces, groups, and audit entries. When syncing, it first asks Atlassian which Confluence sites the current OAuth grant can reach. OAuth is the permission system where the user grants access without handing over a password. Then it loops through each site and calls the right Confluence API path.

Confluence does not provide a simple “only send records newer than this date” option for these streams, so the connector pages through results and filters them locally using a saved cursor, like checking every item on a shelf but only keeping the ones newer than your bookmark. It also prefixes record IDs with the site ID so two different Confluence sites cannot accidentally produce the same record reference.

Finally, for pages, posts, comments, and space descriptions, it strips markup down to human-readable text. If Confluence refuses access because the grant lacks permission, the stream is marked as skipped rather than failed.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the readable body HTML stored inside a Confluence record. It looks for the storage-format body first, then falls back to the rendered view body if needed.

**Data flow**: It receives one Confluence record as a dictionary-like object. It reads nested fields such as `body.storage.value` and `body.view.value`; if one contains a non-empty string, it returns that string. If no usable body text is present, it returns nothing.

**Call relations**: During record cleanup, `ConfluenceConnector.flatten` calls this helper when it is preparing pages, blog posts, and comments. The helper gives `flatten` the raw body content that later rendering can turn into readable prose.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches batches of records for one Confluence stream, across all Confluence sites the current grant can access. It is the main reading loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the newest record already synced. It finds the API path for the stream, asks `_sites` for reachable Confluence sites, then asks `_offset_results` for pages of records from each site. Before yielding each batch, it adds site context such as the Confluence cloud ID and site URL. If Atlassian replies with a permission error, it turns that into a skipped stream instead of a hard failure.

**Call relations**: The sync framework calls this when it wants records from a Confluence stream. `paginate` coordinates the site lookup, page-by-page API reading, and context tagging. It hands record batches back to the framework for later flattening, rendering, and storage.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Asks Atlassian which Confluence sites the current OAuth permission grant can reach. This matters because one user grant may cover more than one Confluence site.

**Data flow**: It receives an HTTP client and calls Atlassian’s accessible-resources endpoint. It turns the JSON response into a list, or an empty list if the response is missing or not shaped as expected. The result is a list of site records, each usually including an ID and URL.

**Call relations**: `ConfluenceConnector.paginate` calls this before reading any stream data. The site IDs returned here become the `cloud_id` used to build every site-specific Confluence API URL.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through a Confluence API collection one page at a time. It also performs local incremental filtering, keeping only records newer than the saved cursor when a cursor is available.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the field to compare against that cursor. It sends requests with `start` and `limit` values, extracts the `results` list from each response, filters out old records if needed, yields non-empty batches, and stops when there are no records or the API response has no next-page link.

**Call relations**: `ConfluenceConnector.paginate` calls this for each reachable Confluence site. It supplies the actual record batches that `paginate` then decorates with site information before passing them onward.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Reshapes raw Confluence API records into a flatter, more consistent form that the rest of the sync system can index and compare. It gives important fields clear names such as title, body, URL, author, and timestamps.

**Data flow**: It receives a raw record and the stream it came from. Depending on the stream, it copies useful fields, builds a browser URL from the site URL and Confluence web link, extracts body content, lifts nested timestamp fields to easier-to-read keys, and adds a site prefix to the primary ID when needed. It returns the cleaned-up record dictionary.

**Call relations**: After `paginate` has supplied raw records, the connector framework uses `flatten` to normalize each one. It relies on `_body_text` for page-like body content and on path lookups for nested fields such as version timestamps and author IDs.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a flattened Confluence record into the title and text that should be remembered or searched. For page-like content, it removes Confluence’s HTML-like markup so the stored text reads like normal prose.

**Data flow**: It receives a flattened record and its stream description. For pages, blog posts, and comments, it takes the title and body and runs the body through `_StorageTextExtractor`. For spaces, it uses the name or key as the title and extracts text from the description. For other streams, it falls back to the parent connector’s default rendering. It returns a pair: the title and the final text block.

**Call relations**: The sync framework calls this after records have been fetched and flattened. It calls `_str` to safely read string titles and uses `_StorageTextExtractor` to convert Confluence markup into readable text before handing the result back for indexing or recall.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Sets up a small HTML text reader used to collect only the human-readable words from Confluence body markup. It also enables automatic unescaping of HTML entities, so things like `&amp;` become `&`.

**Data flow**: It starts with no input besides the new object being created. It initializes the base HTML parser and creates an empty list where pieces of text and line breaks will be collected. The result is a ready-to-use parser instance.

**Call relations**: `_StorageTextExtractor.extract` creates this parser when it needs to clean a raw Confluence body. The parser’s later callback methods fill the text list as the markup is read.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Provides the simple public entry point for converting Confluence storage-format HTML into plain text. Callers can give it any value, and it safely returns an empty string if the value is not usable text.

**Data flow**: It receives a raw value that may or may not be a string. If it is not a non-empty string, it returns an empty string. Otherwise it creates a parser, feeds the markup into it, asks the parser to assemble the cleaned text, and returns that text.

**Call relations**: `ConfluenceConnector.render` uses this method when preparing pages, blog posts, comments, and space descriptions for recall. Internally, it drives the parser callbacks such as `handle_data`, `handle_starttag`, and `handle_endtag`, then finishes with `_text`.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Collects the actual readable words found between markup tags. This is where the page’s visible text is preserved.

**Data flow**: The HTML parser gives it a piece of text from inside the markup. It appends that text to the parser’s internal list of parts. Nothing is returned; the parser’s stored parts are changed.

**Call relations**: This is called automatically while `_StorageTextExtractor.extract` feeds markup into the parser. Later, `_text` joins and cleans the collected pieces.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when an opening tag represents a block-like part of the page, such as a paragraph, heading, table cell, or list item. This helps the final text keep a readable shape instead of becoming one long run-on line.

**Data flow**: The HTML parser gives it a tag name and attributes. If the tag is one of the known block tags, it appends a newline marker to the internal text parts. It ignores attributes and returns nothing.

**Call relations**: This is called automatically during `_StorageTextExtractor.extract`. Its newline markers are later cleaned up by `_text` so the output has sensible line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag closes. This keeps separate paragraphs, headings, list items, and table cells from being glued together.

**Data flow**: The HTML parser gives it the closing tag name. If the tag is one of the known block tags, it appends a newline marker to the internal text parts. It returns nothing and only changes the parser’s collected parts.

**Call relations**: This is called automatically while `_StorageTextExtractor.extract` reads the markup. Together with `handle_starttag`, it gives `_text` enough boundary markers to produce readable lines.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Builds the final clean plain text from all collected pieces. It removes extra spaces, drops empty lines, and trims the result.

**Data flow**: It reads the parser’s internal list of text fragments and newline markers. It joins them, splits the result into lines, normalizes repeated whitespace inside each line, removes blank lines, and returns the cleaned string.

**Call relations**: `_StorageTextExtractor.extract` calls this after the parser has finished reading the raw Confluence markup. This is the final step before cleaned body text goes back to `ConfluenceConnector.render`.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. It prevents titles and names from accidentally becoming misleading text like Python object representations.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. If not, it returns an empty string.

**Call relations**: `ConfluenceConnector.render` calls this when choosing titles for pages, posts, comments, and spaces. It acts as a small guardrail before the rendered text is assembled.

*Call graph*: called by 1 (render).


### Teams collaboration
Reads Microsoft Teams teams, channels, chats, and messages through Microsoft Graph for later storage and search.

### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `source sync and record rendering`

This connector is the bridge between the project and Microsoft Teams. Without it, the system would not know where to ask Microsoft for Teams data, how to follow Microsoft’s paged responses, or how to turn Teams messages into readable text.

Microsoft Graph returns lists in chunks, a bit like a book split across many pages. The connector walks through those pages for the signed-in user’s joined teams, each team’s channels, each channel’s messages, the user’s chats, and each chat’s messages. It adds helpful context as it goes, such as which team a channel came from or which chat a message belongs to, so records do not arrive as loose, unexplained fragments.

Messages can be synced incrementally. That means the connector can use a saved timestamp, called a cursor or watermark, and only return messages changed after that point. This avoids rereading everything on every run.

The file is careful about permissions. If Microsoft refuses access to all teams or chats, the stream is marked as skipped instead of crashing the whole sync. If one team, channel, or chat cannot be read, the connector skips that parent and keeps going. For display, message bodies are cleaned up by removing simple HTML tags, because Microsoft stores message content as HTML.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the full list of Teams that the signed-in user has joined. Other parts of the connector use this as the starting point before looking for channels and channel messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/joinedTeams`, follows each returned page, gathers all team records into one list, and returns that list.

**Call relations**: When the connector needs teams directly, `MicrosoftTeamsConnector.paginate` calls this function. When it needs channels, `MicrosoftTeamsConnector._channels` calls it first so it knows which teams to inspect.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside each joined team. It also labels each channel with the team it came from, so later records keep their place in the Teams structure.

**Data flow**: It receives an authenticated HTTP client. It first gets all teams from `MicrosoftTeamsConnector._teams`, then asks Microsoft Graph for each team’s channels. For every page of channels, it adds context such as the team ID and team name, then yields that page onward. If one team cannot be read because access is denied or it no longer exists, it skips that team and continues.

**Call relations**: This function sits between team discovery and message discovery. `MicrosoftTeamsConnector.paginate` calls it when syncing the channel stream, and `MicrosoftTeamsConnector._channel_messages` calls it when it needs the channels whose messages should be read.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from every channel the user can access. It can return only messages changed after a saved cursor, which keeps repeat syncs smaller and faster.

**Data flow**: It receives an authenticated HTTP client and an optional cursor timestamp. It gets channels from `MicrosoftTeamsConnector._channels`, asks Microsoft Graph for messages in each channel, filters out older messages when a cursor is present, adds team, channel, and thread context, and yields non-empty batches. If one channel cannot be read because of a permission or missing-resource error, it skips that channel.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the requested stream is channel messages. This function depends on `MicrosoftTeamsConnector._channels` to know where to look, then passes enriched message batches back to the sync runner.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s chats. This is the starting point for reading one-to-one and group chat messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/chats`, follows all pages of results, gathers the chat records into one list, and returns that list.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when syncing chats directly. `MicrosoftTeamsConnector._chat_messages` calls it first so it knows which chats to visit for messages.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from each chat the user can access. Like channel message syncing, it supports a cursor so only newer or changed messages need to be returned.

**Data flow**: It receives an authenticated HTTP client and an optional cursor timestamp. It fetches chats from `MicrosoftTeamsConnector._chats`, requests messages for each chat, filters out messages that are not newer than the cursor, adds chat and thread context, and yields batches that contain messages. If one chat cannot be read because it is forbidden or missing, it skips that chat and continues.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when syncing chat messages. It uses `MicrosoftTeamsConnector._chats` for the list of chats, then hands enriched message batches back to the broader sync process.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading each Microsoft Teams stream. Given a stream name, it chooses the right helper to fetch teams, channels, messages, chats, or chat messages.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields pages of records to the caller. If Microsoft rejects the top-level request because the user or app lacks permission, it raises a skip signal rather than treating the whole run as a hard failure. If the stream name is unknown, it also reports that the stream is not implemented.

**Call relations**: The source sync framework calls this function when it wants records for a particular stream. This function then hands work to `MicrosoftTeamsConnector._teams`, `MicrosoftTeamsConnector._channels`, `MicrosoftTeamsConnector._channel_messages`, `MicrosoftTeamsConnector._chats`, or `MicrosoftTeamsConnector._chat_messages`, depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into readable text for storage or search. Message records get special treatment because Microsoft stores their body as HTML.

**Data flow**: It receives one record and its stream description. For non-message streams, it lets the parent connector use the normal titled JSON rendering. For channel and chat messages, it reads the subject, pulls `body.content` from inside the record, removes simple HTML tags, builds a heading, and returns both a title and a text body.

**Call relations**: The sync framework calls this after records have been fetched and need to become human-readable pages. This function uses `_str` to safely read the subject, `get_path` to reach nested body content, and `_strip_html` to clean the message text.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a message body so the stored text is easier to read. This is useful because Microsoft Graph sends Teams message content as HTML rather than plain text.

**Data flow**: It receives any value. If the value is not text, it returns nothing. If it is text, it replaces HTML-looking tags with spaces, trims extra space at the ends, and returns the cleaned string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when preparing channel or chat messages for display. It is a small cleanup step before the rendered page text is returned.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a possible title value into a string only when it already is one. It prevents non-text values from accidentally appearing as noisy titles.

**Data flow**: It receives any value. If the value is a string, it returns that string. Otherwise, it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. It helps the renderer build a clean heading even when Microsoft returns no subject or returns an unexpected value.

*Call graph*: called by 1 (render).


### Notion workspaces
Reads Notion users, pages, databases, comments, and blocks as human-readable searchable content.

### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `source sync`

Notion stores information in a very nested way: pages have properties, pages contain blocks, blocks can contain more blocks, and readable text is often hidden inside “rich text” lists. If the system simply saved Notion’s raw JSON, a person searching later would see a pile of structure instead of the words they remember reading. This connector is the translator between Notion and the rest of the system.

It defines the Notion streams the system can sync: users, pages, data sources, comments, and blocks. For pages and data sources, it uses Notion search and sorts by edit time so later syncs can skip old records. For blocks, it walks through each page’s block tree, like opening folders inside folders, but stops at a safe depth and does not dive into child pages or databases because those are treated as separate records. For comments, it asks Notion for comments on each page. For users, it reads the user list directly.

The file also protects the sync from expected permission problems. If Notion says the integration is not allowed to see a stream, the connector marks that stream as skipped instead of crashing the whole run. Finally, its rendering code extracts page titles, property text, block text, comment text, and user names/emails into prose that can be recalled later.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Notion and adds the required Notion API version header. This matters because Notion requires clients to say which API version they expect.

**Data flow**: It receives a base URL and a credential reference. It asks the parent REST connector to build the normal web client, then adds the Notion-Version header. It returns that configured client, ready for Notion API requests.

**Call relations**: This fits into the connector setup before any Notion data is fetched. The inherited REST machinery creates the client through this override, and later pagination methods use that client for their API calls.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Notion reading method for each stream and yields records in pages. It is the main dispatcher for syncing Notion data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the last saved point from a previous sync. It checks the stream name, calls the matching reader, and passes each batch of records onward. If Notion refuses access with a permission-related status, it turns that into a skipped stream instead of a failed sync.

**Call relations**: The broader sync process calls this when it wants records from a Notion stream. Depending on the stream, it hands work to _collection for users, _search for pages and data sources, _comments for comments, or _blocks for block bodies. If the stream is unknown or Notion denies access, it raises StreamSkipped so the run can record a clean skip.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Notion search results for pages or data sources, one API page at a time. It also filters out records that are not newer than the saved sync cursor.

**Data flow**: It receives an HTTP client, an object type such as page or data_source, and an optional cursor timestamp. It sends repeated POST requests to Notion search, asks for results sorted by last edit time, turns the results field into a safe list, removes records at or before the cursor, and yields non-empty batches. It stops when Notion says there are no more pages.

**Call relations**: paginate calls this directly for pages and data sources. _blocks and _comments also call it first to discover which pages exist before asking for each page’s blocks or comments. It uses list_or_empty to avoid breaking if the API response has a missing or non-list results value.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds every page and then reads the block content inside those pages. This is how the connector captures the actual body text of Notion pages, not just their page properties.

**Data flow**: It receives an HTTP client and an optional cursor. It first searches for all pages without applying the block cursor to the page search, then extracts each page ID. For each valid page ID, it asks _block_children to walk the page’s block tree and yields the batches of blocks it returns.

**Call relations**: paginate calls this when the sync asks for the blocks stream. _blocks relies on _search to find pages, then hands each page ID to _block_children because the real block traversal is recursive and belongs there.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through the children of one Notion block or page and yields block records, including nested blocks. It is careful not to recurse forever or descend into record types that should be synced separately.

**Data flow**: It receives an HTTP client, a block ID, the current nesting depth, and an optional cursor. If the depth is too high, it stops. Otherwise it fetches child blocks, filters out blocks that are not newer than the cursor, yields the remaining blocks, and then repeats the process for child blocks that have children and are safe to descend into.

**Call relations**: _blocks calls this for each page. During traversal, this function calls _collection to fetch each page of child blocks from Notion. It also calls itself recursively for nested blocks, like walking down a tree branch by branch.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads comments attached to every Notion page. Comments are not found through the main page search results, so this function has to ask for them page by page.

**Data flow**: It receives an HTTP client and an optional cursor. It searches for pages, extracts each valid page ID, fetches comments for that page, filters out comments older than or equal to the cursor, and yields any remaining comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find the pages to inspect, then uses _collection to call Notion’s comments endpoint for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the common loop for Notion endpoints that return a results list and a next cursor. It keeps the other reader functions from repeating the same paging code.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the parent REST helper to fetch cursor-based pages using Notion’s field names, then yields each list of records it receives.

**Call relations**: paginate uses this directly for users. _block_children uses it for block children, and _comments uses it for page comments. It is the shared small conveyor belt that turns Notion’s paginated API responses into batches for the sync.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Notion record into a title and readable text. This is important because Notion’s useful words are buried in different places depending on whether the record is a page, block, comment, data source, or user.

**Data flow**: It receives one record and its stream description. It chooses extraction helpers based on the stream: page title and properties for pages, rich text for data sources and comments, block text for blocks, and name/email text for users. It then builds a heading and body string and returns both the short title and the full rendered text.

**Call relations**: The sync/rendering layer calls this after records have been fetched. It delegates the small extraction jobs to _page_title, _properties_text, _rich_text_text, _block_text, _user_text, and _str. For unknown streams, it falls back to the parent connector’s rendering behavior.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only when it is already a string. It prevents accidental display of non-text values where the renderer expects plain text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: render, _block_text, _property_text, and _user_text call this whenever they pull optional text-like fields from Notion data. It acts as a small guardrail around Notion’s flexible JSON values.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: Extracts readable words from Notion’s rich text format. Rich text is Notion’s list of text runs, where each run may contain a plain_text field.

**Data flow**: It receives a value that should be a list of rich text parts. If it is not a list, it returns an empty string. If it is a list, it takes the plain_text from each valid part, joins those pieces together, trims outside whitespace, and returns the result.

**Call relations**: render uses this for data source titles, descriptions, and comments. _page_title, _property_text, and _block_text also call it when they need to turn Notion rich text into normal text.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: Finds the title of a Notion page from its properties. Notion page titles are stored as a special property rather than as a simple top-level field.

**Data flow**: It receives a page record. It looks inside the properties object, searches for the property whose type is title, converts that title’s rich text into plain text, and returns the first non-empty title it finds. If there is no usable title, it returns an empty string.

**Call relations**: render calls this when preparing page records. _page_title relies on _rich_text_text because the title itself is stored in Notion’s rich text shape.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: Turns a Notion page’s visible properties into simple lines of text. This helps page metadata, such as status, dates, people, or URLs, become searchable prose.

**Data flow**: It receives a page record. It reads the properties dictionary, converts each supported property value into text, formats each non-empty one as 'name: value', and joins those lines with newlines. If the page has no usable properties, it returns an empty string.

**Call relations**: render calls this for page records after finding the page title. It delegates the type-specific conversion of each property to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: Converts one Notion property into plain text when the property type is supported. It knows how to read common page fields such as titles, rich text, select values, people lists, dates, numbers, links, email addresses, phone numbers, and checkboxes.

**Data flow**: It receives one property dictionary. It checks the property’s type, finds the value stored under that type name, and converts it to a string-like form using the right rule for that type. Unsupported or malformed properties become an empty string.

**Call relations**: _properties_text calls this once for each property on a page. This function uses _rich_text_text for rich text fields and _str for optional string fields such as select names and dates.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: Extracts the readable text from one Notion block. Blocks are the pieces inside a page, such as paragraphs, headings, list items, to-do items, child pages, and child databases.

**Data flow**: It receives a block record. It looks up the block’s type-specific content, returns child page or child database titles directly, otherwise extracts rich text. For to-do blocks, it prefixes the text with a checked or unchecked marker. If the block shape is not usable, it returns an empty string.

**Call relations**: render calls this for records in the blocks stream. It uses _rich_text_text for normal block text and _str for child page or child database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: Builds a small readable profile for a Notion user. It includes the user’s name and, when present, their email address.

**Data flow**: It receives a user record. It reads the top-level name and the email nested under the person field, keeps only real strings, joins the available parts with a newline, and returns the result.

**Call relations**: render calls this for user records. It uses _str so missing or non-string name and email fields do not leak strange values into the rendered text.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Outlook communication stores
Reads Outlook mail, conversations, contacts, calendar events, and folders while tracking incremental sync state.

### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `sync run`

This connector is the bridge between the project and a user’s Outlook account. It talks to Microsoft Graph, which is Microsoft’s web API for mailbox and calendar data. Without this file, the system would not know how to pull Outlook records, how to continue a previous sync, or how to notice deleted items.

The main idea is incremental syncing. Microsoft Graph offers “delta” endpoints: the first request walks through all matching data page by page, and the final response gives back a special link that means “next time, resume from here.” This file stores that link as the cursor. For messages and contacts, Outlook data is split across folders, so the cursor is a small JSON map from folder ID to that folder’s resume link.

The connector also shapes raw Microsoft records into fields the rest of the system expects. For example, it turns a contact’s first email address into `email`, a message’s sender into `from_handle`, and an event’s HTML body into plain text. Conversations are not a separate Outlook object here; they are built by reading messages and grouping them by `conversationId`, like sorting letters into thread folders.

If Microsoft refuses access because the connected account lacks the right permission, the connector reports the stream as skipped rather than crashing the whole sync.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: Formats a Python date and time into the exact UTC timestamp style Microsoft Graph expects in filters. It is used when the connector asks Graph for only records after a certain point in time.

**Data flow**: It receives a datetime value → converts it to UTC → returns a string like `2024-01-01T12:00:00Z` that can be placed into a Graph query.

**Call relations**: When conversation or message syncing needs a starting time, those flows call this helper before sending the request to Microsoft Graph.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Turns a simple HTML string into plain text by removing tags. This is useful for calendar event descriptions, which often arrive from Microsoft as HTML.

**Data flow**: It receives any value → if the value is not a string, it returns nothing → if it is a string, it replaces HTML tags with spaces, trims the result, and returns the cleaned text.

**Call relations**: The `flatten` step calls this when preparing event records so the rest of the system gets a readable description instead of raw HTML markup.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address in a contact record. Outlook contacts can contain several email addresses, but the system wants one convenient main email field.

**Data flow**: It receives a contact dictionary → looks through its `emailAddresses` list → reads each nested `emailAddress.address` value → returns the first non-empty email string, or nothing if none is found.

**Call relations**: The contact branch of `flatten` uses this helper while turning Microsoft’s contact shape into the project’s standard contact fields.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from a contact record. It prefers the mobile phone number, then falls back to the first business phone number.

**Data flow**: It receives a contact dictionary → checks `mobilePhone` first → if that is missing, scans `businessPhones` → returns the first non-empty phone string, or nothing.

**Call relations**: The contact branch of `flatten` calls this so synced contacts have a simple `phone` field instead of forcing later code to understand Outlook’s phone layout.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the connector’s public paging entry for the sync framework. It passes through the stream, cursor, and optional backfill date to the Outlook-specific pagination logic.

**Data flow**: It receives an HTTP client, a stream description, the previous cursor, the current user ID, and possibly a backfill cutoff date → forwards the relevant values to `paginate` → yields pages of records or stream pages back to the sync runner.

**Call relations**: The broader source framework calls this method when it wants Outlook data. This method immediately hands the real work to `OutlookConnector.paginate`, adding support for the pinned backfill window used by mail-related streams.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right Outlook sync routine for the requested stream. It is the traffic director for contacts, messages, conversations, events, and mail folders.

**Data flow**: It receives a stream name plus the current cursor and optional backfill date → routes to the matching helper method → yields each page produced by that helper. If Microsoft replies with an authorization refusal, it turns that into a clean skipped-stream result; if the stream is unknown, it also reports it as skipped.

**Call relations**: `paginate_source` calls this during a sync. From there it delegates to `_conversation_pages`, `_message_delta_pages`, `_contact_delta_pages`, `_event_delta_pages`, or `_graph_delta_pages`, depending on what the runner asked to sync.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records from Outlook messages. Microsoft messages contain a `conversationId`, so this method groups messages with the same ID into one email-thread record.

**Data flow**: It receives an HTTP client, a cursor, and possibly a backfill cutoff → asks `/me/messages` for messages ordered by last modification time → groups them by conversation ID → keeps the earliest creation time and latest update details for each thread → yields one list of conversation summaries.

**Call relations**: `paginate` calls this when the conversations stream is requested. It uses `_graph_instant` when it needs to turn a backfill date into a Microsoft Graph filter.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta endpoint page by page. Delta endpoints are Graph’s way of saying “give me what changed since last time.”

**Data flow**: It receives an initial API path, an optional saved cursor link, and optional query parameters → requests the current page from Graph → separates normal records from deleted records marked with `@removed` → yields a `StreamPage` containing records, deletion IDs, and the next cursor → continues while Graph provides a next-page link.

**Call relations**: This is the shared engine behind several stream-specific syncs. `paginate` uses it directly for mail folders, while message, contact, and event sync helpers call it for their own Microsoft Graph delta endpoints.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs Outlook messages across every mail folder while keeping a separate resume point for each folder. This matters because Microsoft Graph tracks message changes folder by folder here.

**Data flow**: It receives an HTTP client, a saved cursor map, and possibly a backfill cutoff → decodes the cursor map → lists mail folders → for each folder, resumes from that folder’s saved delta link or starts a new delta walk → adds `mail_folder_id` to each message → updates the cursor map → yields stream pages with records, deletes, and the encoded new cursor.

**Call relations**: `paginate` calls this for the messages stream. It relies on `_list_mail_folders` to discover folders, `_graph_delta_pages` to read each folder, `_graph_instant` for the first backfill filter, and the cursor encode/decode helpers to preserve per-folder progress.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs Outlook contacts from the default contact area and any contact folders. Like messages, it tracks progress separately for each contact location.

**Data flow**: It receives an HTTP client and a saved cursor map → decodes the map → builds a list containing the default contacts plus contact folders → runs the appropriate delta endpoint for each one → updates that folder’s cursor when Graph gives a new link → yields pages with records, deletes, and the encoded cursor map. If the default contacts endpoint is missing or unsupported, it quietly moves on.

**Call relations**: `paginate` calls this for the contacts stream. It uses `_list_contact_folders` to find extra folders, `_graph_delta_pages` to do the actual Graph reading, and the cursor helpers to keep each folder’s place.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs calendar events within a broad time window around the current date. It looks one year back and two years ahead, which keeps the calendar useful without asking for all time.

**Data flow**: It receives an HTTP client and cursor → calculates today’s date in UTC → builds start and end date parameters for Microsoft’s calendar view delta endpoint → yields the pages returned by `_graph_delta_pages`.

**Call relations**: `paginate` calls this when the events stream is requested. It hands the actual delta paging work to `_graph_delta_pages` after preparing the calendar-specific time range.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s Outlook mail folders. The message sync needs these IDs so it can sync each folder separately.

**Data flow**: It receives an HTTP client → requests `/me/mailFolders` in pages → reads each folder’s `id` → returns a list of valid folder ID strings.

**Call relations**: `_message_delta_pages` calls this before syncing messages. The returned folder IDs become the set of per-folder delta walks.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s Outlook contact folders. Contact syncing uses this to include contacts outside the default contact list.

**Data flow**: It receives an HTTP client → requests `/me/contactFolders` in pages → reads each folder’s `id` → returns a list of valid folder ID strings.

**Call relations**: `_contact_delta_pages` calls this before syncing folder-based contacts. The default contacts area is added separately, then these folder IDs are used for the rest.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts raw Microsoft Graph records into friendlier records with common fields the rest of the system expects. It keeps the original record data but adds easier names like `email`, `snippet`, `sent_at`, or `start_at`.

**Data flow**: It receives one raw record and its stream description → checks which stream the record belongs to → adds stream-specific fields for contacts, messages, or events → returns the enriched dictionary. For unknown or already-simple streams, it returns the record unchanged.

**Call relations**: After pages are fetched, the source framework can call this to normalize individual records. It uses `_first_email` and `_phone` for contacts, `_strip_html` for event descriptions, and nested-path lookups for Microsoft fields buried inside sub-objects.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Reads the saved per-folder cursor string back into a dictionary. This lets message and contact syncs remember a different Microsoft resume link for each folder.

**Data flow**: It receives a raw cursor string or nothing → if empty or invalid, returns an empty dictionary → if it is valid JSON and shaped like a dictionary, keeps only non-empty string values → returns a clean folder-to-cursor map.

**Call relations**: `_message_delta_pages` and `_contact_delta_pages` call this at the start of their work so they know where each folder’s previous sync stopped.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Turns a per-folder cursor dictionary into a stable JSON string for storage. This is how the connector saves progress between sync runs.

**Data flow**: It receives a dictionary of folder IDs to cursor links → if the dictionary is empty, returns nothing → otherwise serializes it as sorted JSON text → returns that text as the next cursor.

**Call relations**: `_message_delta_pages` and `_contact_delta_pages` call this after Graph provides new resume links, so the sync runner can store one cursor value that contains all folder positions.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### Slack workspace messaging
Reads Slack users, channels, messages, threads, and senders from a workspace without writing back to Slack.

### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `during Slack source sync runs`

Slack does not hand over a whole workspace in one simple response. It gives results in pages, uses special cursor tokens to fetch the next page, and sometimes reports an error inside a normal-looking successful HTTP response. This file hides those Slack-specific details behind a connector called SlackConnector.

The connector first defines the streams it can produce: users, conversations, conversation threads, messages, and message participants. For users and conversations, each sync is treated like a fresh snapshot. If a user or channel disappears from what Slack allows this app to see, the system can mark it as gone.

Messages are more careful. Slack history is read channel by channel, newest first. A helper from the source SDK, PartitionWalk, keeps a separate position for each channel, like putting a bookmark in every book instead of one bookmark for the whole shelf. That prevents a busy channel from causing quiet channels to be skipped. Each raw Slack message page is then reshaped into whichever stream is being requested: message records, thread records, or participant records.

The file also makes permission failures less disruptive. If the app lacks a broad permission, the stream is skipped rather than treated as a broken sync. If only one channel refuses history access, that channel is skipped while the rest continue.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error object when Slack says a request failed, even if the HTTP request itself looked successful. It preserves Slack’s error code and, when Slack provides it, the missing permission scope.

**Data flow**: It receives an error name and an optional needed permission → builds a readable error message and stores those details on the object → returns an exception ready to be raised and later inspected.

**Call relations**: When _ok_or_raise sees Slack’s ok field set to false, it calls this constructor. Later code checks the stored error code to decide whether to skip a stream, skip one channel, or fail normally.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard entry point the broader source-sync system uses to ask this connector for pages of Slack records. It mostly passes the request through to the connector’s main pagination logic.

**Data flow**: It receives an HTTP client, a stream description, a saved cursor, the current bot user id, and an optional backfill date → forwards those values unchanged → returns an asynchronous stream of record pages.

**Call relations**: The SDK calls this method when it wants Slack data. This method delegates to SlackConnector.paginate so all stream-specific choices live in one place.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses how to read the requested Slack stream. It knows that users, conversations, and message-derived streams each need a different walking strategy.

**Data flow**: It receives the requested stream and sync state → for users or conversations, it yields simple API pages; for message streams, it first gathers users and channels, then walks each channel’s message history with per-channel cursors → yields pages of normalized records or raises a skip for unsupported streams.

**Call relations**: It is called by paginate_source. It calls iter_users, iter_conversations, user_index, _slack_ts, and sets up PartitionWalk so _channel_pages can read each channel without losing its place.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies PartitionWalk with the list of Slack channel ids that should be walked for messages. Think of it as handing over the names of all the books on the shelf before reading their pages.

**Data flow**: It reads the already-built channel dictionary → yields one channel id at a time → gives PartitionWalk the set of independent channel partitions to track.

**Call relations**: This small helper is created inside SlackConnector.paginate when reading message-related streams. PartitionWalk uses it to know which channels should have their own cursor and history window.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects PartitionWalk’s request for one channel’s next slice of history to the Slack-specific code that fetches that slice. It adapts the generic walking helper to Slack channels.

**Data flow**: It receives a channel id and a time/window bound from PartitionWalk → looks up the channel details and passes them, along with users and bot identity, into _channel_pages → returns an asynchronous sequence of WalkPage objects for that channel.

**Call relations**: This helper is created inside SlackConnector.paginate. PartitionWalk calls it whenever it needs another page from a particular channel, and it hands the work to SlackConnector._channel_pages.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users in pages and converts each user into the system’s simpler user shape. This is how the sync learns who exists in the workspace.

**Data flow**: It starts without a Slack cursor → calls Slack’s users.list endpoint, flattens valid member objects, yields non-empty pages, then follows Slack’s next cursor until there is no next page → outputs batches of user dictionaries.

**Call relations**: SlackConnector.paginate calls this directly for the users stream. SlackConnector.user_index also calls it to build a lookup table used later when formatting messages and participants.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including public channels, private channels, direct messages, and group messages. It reshapes Slack’s channel objects into records the rest of the system can understand.

**Data flow**: It sends paged conversations.list requests with the desired conversation types → for each channel, extracts identifiers, names, type flags, topic, purpose, creator, and timestamps → yields batches until Slack gives no next cursor.

**Call relations**: SlackConnector.paginate calls this for the conversations stream and also uses it before message syncs to discover which channels should be walked.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table from Slack user id to normalized user record. Message processing uses this to attach useful sender information, such as email or display name.

**Data flow**: It reads every page from iter_users → stores each user by its id → returns a dictionary that can answer 'who is this user id?' quickly.

**Call relations**: SlackConnector.paginate calls this before walking message streams. _flatten_message and _participant_for_message then benefit from the user details passed down through _channel_pages and _message_page.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Fetches one channel’s message history from Slack within the time window requested by PartitionWalk. It turns Slack history responses into walking pages while treating unreadable channels as skippable.

**Data flow**: It receives a channel, a stream, a time bound, user lookup data, and the connector’s own bot user id → sends conversations.history POST requests with oldest/latest/cursor parameters → filters raw messages with timestamps, converts each page through _message_page, follows next cursors, and stops when the channel is exhausted.

**Call relations**: PartitionWalk reaches this through the channel_pages helper inside SlackConnector.paginate. It calls _slack_post for Slack API access, _message_page to derive records, and raises PartitionSkipped when Slack refuses only this channel.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific record type requested: threads, messages, or participants. It also reports the timestamp range of that raw page so the channel walk can advance safely.

**Data flow**: It receives raw Slack messages plus channel and user context → skips deletion notices except for collecting delete ids, drops messages from this connector’s own live bot user, builds message rows, possible thread rows, and sender participant rows → returns a WalkPage containing the requested records, plus high and low Slack timestamps.

**Call relations**: SlackConnector._channel_pages calls this after each conversations.history response. It relies on _flatten_message, _conversation_thread_from_message, and _participant_for_message to create the different record shapes.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs a top-level Slack listing request, such as users or conversations, and turns broad permission refusals into a clean stream skip. This keeps missing Slack scopes from looking like system crashes.

**Data flow**: It receives an HTTP client, API path, and query parameters → calls _slack_get → if Slack or HTTP status shows a missing-permission style refusal, raises StreamSkipped; otherwise returns the response data or re-raises the error.

**Call relations**: iter_users and iter_conversations call this for their listing endpoints. It sits between those high-level iterators and the lower-level _slack_get error checking.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a Slack GET request and applies Slack’s special success/error rules. Slack can return HTTP 200 while still saying ok=false, so this helper checks the response body too.

**Data flow**: It receives an HTTP client, path, and optional query parameters → uses the base REST connector to make the GET request → passes the decoded response to _ok_or_raise → returns valid Slack data or raises SlackApiError.

**Call relations**: SlackConnector._enumerate calls this for users.list and conversations.list. It hands Slack’s response to _ok_or_raise so higher layers can reason about Slack error codes.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a Slack POST request and checks whether Slack marked the response as failed. It is used for Slack APIs that expect data in the request body, such as channel history.

**Data flow**: It receives an HTTP client, path, and optional JSON body → uses the base REST connector to make the POST request → sends the decoded response through _ok_or_raise → returns valid data or raises SlackApiError.

**Call relations**: SlackConnector._channel_pages calls this when reading conversations.history. _ok_or_raise provides the Slack-specific error object that _channel_pages can use to skip an unreadable channel.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks Slack’s ok flag and turns Slack-level failures into exceptions. This is needed because Slack often reports application errors inside the response body instead of only through HTTP status codes.

**Data flow**: It receives a decoded Slack response dictionary → if ok is false, extracts the error code and optional needed scope and raises SlackApiError; otherwise returns the same dictionary unchanged.

**Call relations**: _slack_get and _slack_post both call this after network requests. It calls SlackApiError.__init__ when Slack reports a failure.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Extracts Slack’s next-page token from a response. This lets callers keep asking for more pages until Slack says there are no more.

**Data flow**: It receives a response dictionary → looks inside response_metadata.next_cursor → returns a non-empty cursor string, or None if there is no usable next cursor.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this after each Slack page. Its result decides whether those loops continue or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts a Unix timestamp, meaning seconds since the start of 1970, into a standard readable UTC time string. It safely returns nothing for invalid values.

**Data flow**: It receives any value → rejects booleans and values that cannot be read as a number → converts valid seconds into an ISO-formatted UTC datetime string or returns None.

**Call relations**: iter_conversations uses this for channel creation times. _flatten_user uses it for user update times.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a Python datetime into Slack’s timestamp string format for message-history comparisons. It pads the number so string sorting works correctly across old dates.

**Data flow**: It receives an optional datetime → if absent or before the Unix epoch, returns None; otherwise converts it to seconds with six decimal places and fixed width → returns a Slack-style timestamp string.

**Call relations**: SlackConnector.paginate calls this when setting a backfill floor for PartitionWalk. That floor tells message history not to walk older than the requested backfill point.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts a Slack message timestamp string into a standard UTC time string. This makes Slack message times usable in the system’s normal record fields.

**Data flow**: It receives a Slack timestamp string or None → if empty or not numeric, returns None; otherwise converts it to a UTC ISO datetime string → outputs that readable time.

**Call relations**: _flatten_message uses this for message sent times. _conversation_thread_from_message uses it for thread creation, update, and latest-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s nested user object into a simpler user record. It pulls useful identity fields, contact details, display names, flags, and update time into predictable keys.

**Data flow**: It receives one raw Slack member dictionary → reads the profile sub-object when present, cleans and lowercases email, chooses the best display and real names, converts update time → returns one normalized user dictionary.

**Call relations**: iter_users calls this for every valid Slack member. It uses _first_text to choose the first useful name and _unix_to_iso to format the update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into the system’s message record shape. It also filters out messages posted by this connector’s own bot user so the sync does not ingest its own live bot surface.

**Data flow**: It receives a raw message, its conversation, the user lookup, and the connector’s own user id → validates timestamp and channel, skips the self bot user, chooses thread id, text snippet, sender handle, display name, and timestamps → returns a normalized message dictionary or None if it should not be used.

**Call relations**: SlackConnector._message_page calls this for each raw history item. It uses _slack_ts_to_iso for sent_at, _snippet for compact text, and _first_text to choose a sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread record when a message is either the root of a thread with replies or a reply inside a thread. Plain standalone messages do not become thread records.

**Data flow**: It receives a normalized message plus the original raw Slack message and conversation → checks thread timestamps and reply information → if the message belongs to a real thread, returns a thread summary with title, counts, privacy/archive flags, parent channel, and timestamps; otherwise returns None.

**Call relations**: SlackConnector._message_page calls this after flattening each message. It uses _slack_ts_to_iso to format Slack thread timestamps.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a sender participant record for a message. This records who authored the message in a separate participant stream.

**Data flow**: It receives a normalized message and user lookup → finds the sender’s email or Slack user id as a handle → if no handle can be found, returns None; otherwise returns a participant row tied to the message, channel, and thread.

**Call relations**: SlackConnector._message_page calls this for each accepted message when producing message_participants data. It uses _first_text to choose the best available sender handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Classifies Slack’s many conversation flags into one plain conversation type. It turns combinations of booleans into labels like direct message, group direct message, private channel, or public channel.

**Data flow**: It receives a raw Slack conversation dictionary → checks is_im, is_mpim, is_group, and is_private in priority order → returns a simple type string.

**Call relations**: iter_conversations calls this while reshaping Slack channel records. The resulting type is later carried into conversation and message records.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value from inside nested dictionaries, such as Slack’s topic.value or purpose.value. It avoids crashes when an expected middle object is missing or not a dictionary.

**Data flow**: It receives a dictionary and a path of keys → walks down one key at a time while the current value is still a dictionary → returns the found value, or None if the path cannot be followed.

**Call relations**: iter_conversations calls this to extract topic and purpose text from Slack conversation objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from several candidates. It is used when Slack offers multiple possible names or handles and the connector needs the best available one.

**Data flow**: It receives any number of values → scans them in order, accepting the first string that still has content after trimming spaces → returns that trimmed string or None.

**Call relations**: _flatten_user uses it for names, _flatten_message uses it for sender handles, and _participant_for_message uses it for participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Makes a short, tidy preview of message text. It collapses extra whitespace and limits the preview length.

**Data flow**: It receives message text or None → if text is missing, returns None; otherwise compresses runs of whitespace into single spaces and cuts the result to the snippet cap → returns the preview string or None if nothing remains.

**Call relations**: _flatten_message calls this when building each normalized message. Thread records may later use the message snippet as their title or preview.

*Call graph*: called by 1 (_flatten_message).

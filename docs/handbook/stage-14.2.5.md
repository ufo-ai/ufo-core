# Workspace, document, communication, and support source providers  `stage-14.2.5`

This stage is shared behind-the-scenes support for bringing outside workspace knowledge into the system. Each provider is a connector: it talks to one service’s web API, reads allowed content, and reshapes it into steady “records” or readable pages that the rest of the sync and search system can store, update, and recall.

The knowledge-base and document connectors cover Airtable bases and tables, Confluence spaces and pages, Notion pages and databases, Google Docs, Google Drive files, Google Sheets rows, and Google Meet transcripts or notes. The support connectors bring in Freshdesk and Zendesk tickets, users, help articles, forums, and settings. The developer connector reads GitHub repositories, issues, commits, comments, and releases. Communication connectors read Gmail and Outlook mail, Google Calendar events and attendees, Microsoft Teams teams, chats, channels, and messages, and Slack users, channels, messages, and threads.

Together, these files act like adapters for different plug shapes. Each service speaks differently, but these providers translate them into one common form for search and recall.

## Files in this stage

### Workspace knowledge bases
Connectors that turn structured workspaces, wikis, databases, and collaborative knowledge pages into searchable source records.

### `extensions/sources/ufo_ext_sources/providers/airtable.py`

`io_transport` · `sync data fetching`

Airtable is organized like a set of workspaces: a base contains tables, and a table contains records. There is no single Airtable endpoint that simply says “give me everything.” This connector solves that by walking the structure in order, like opening a filing cabinet, then each drawer, then each folder inside it.

The file defines three readable streams: bases, tables, and records. For bases, it asks Airtable’s metadata API for the list of bases. For tables, it first finds the bases, then asks for the tables in each base. For records, it goes one step deeper: it finds bases, finds their tables, then reads each table’s records in pages of up to 100 items. Airtable uses an “offset” token to point to the next page, so the connector follows that token until there is no more data.

The connector also adds useful origin labels, such as base ID and table ID, so later parts of the system can tell where a record came from. If Airtable rejects access with an authorization error, the connector skips that stream with a clear message instead of crashing the whole sync. This file is read-only; it deliberately does not create or update Airtable data.

#### Function details

##### `AirtableConnector._bases`  (lines 40–42)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Airtable bases available to the current credential. A base is Airtable’s top-level container, similar to a spreadsheet workbook that can contain many tables.

**Data flow**: It receives an HTTP client that already knows how to talk to Airtable. It asks the `/meta/bases` endpoint for data, then pulls the list found under the `bases` field. It returns that list of base dictionaries to the caller.

**Call relations**: This is the first discovery step used by `AirtableConnector.paginate`. Whenever pagination needs bases directly, or needs to find tables or records underneath bases, it starts here before moving deeper into Airtable’s structure.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 44–51)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables inside one Airtable base. It also labels each table with the base it came from, so the table is not separated from its parent context later.

**Data flow**: It receives an HTTP client and one base record. It reads the base’s `id`; if the ID is missing or invalid, it returns an empty list because it cannot safely ask Airtable for tables. Otherwise it calls Airtable’s table metadata endpoint for that base, extracts the `tables` list, and adds `base_id` and `base_name` to each table before returning them.

**Call relations**: This function is called by `AirtableConnector.paginate` after bases have been found. In the tables stream it provides the actual table pages, and in the records stream it supplies the table list that the connector must visit before it can read records.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 53–71)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the records from one specific Airtable table, one page at a time. It keeps each record tied to its base and table so its origin remains clear after syncing.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks the table’s `id`; if the ID is missing or invalid, it stops without yielding anything. Otherwise it requests records from Airtable using Airtable’s cursor-style paging, where each response may include an `offset` token for the next page. For every non-empty page, it adds `base_id`, `table_id`, and `table_name` to the records and yields that page.

**Call relations**: This function is called by `AirtableConnector.paginate` only after the connector has discovered both a base and one of its tables. It is the final step in the discovery chain: bases lead to tables, and tables lead to record pages.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 73–110)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main reader for each Airtable stream. Given a requested stream, it decides whether to return bases, tables, or records, and yields the data in pages for the sync system to consume.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. For the `bases` stream, it fetches bases and yields them as one page. For the `tables` stream, it fetches bases, then tables for each base, collecting them into pages of about 100. For the `records` stream, it fetches bases, then tables, then record pages for every table. If the stream name is unknown, it reports that the stream should be skipped. If Airtable returns a 401 or 403 status, meaning the credential is not allowed to access that data, it turns that into a controlled skip message; other HTTP errors are allowed to continue upward.

**Call relations**: This is the central entry point the wider source-sync framework calls when it wants Airtable data. It coordinates the helper methods `_bases`, `_tables_for_base`, and `_records_for_table`, choosing the right path depending on the stream being synced.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 112–134)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Airtable objects into a more convenient shape for the rest of the system. It adds predictable fields such as API URLs, creation time, and a clean `fields` dictionary for records.

**Data flow**: It receives one Airtable record and the stream it belongs to. For bases, it keeps the original data and adds a metadata API URL. For tables, it adds the table API URL using the stored base ID. For records, it normalizes the record ID, maps Airtable’s `createdTime` into `created_at`, and makes sure `fields` is a dictionary even if Airtable sent something unexpected. It returns the reshaped dictionary without changing the original stream flow.

**Call relations**: After `paginate` has produced raw pages, the broader connector framework can call this function on each item to make it easier to index, display, or search. It does not fetch new data; it prepares already-fetched data for downstream use.


### `extensions/sources/ufo_ext_sources/providers/confluence.py`

`io_transport` · `source sync`

Confluence stores pages as structured XHTML, not as plain writing. If the system saved that raw format, a recalled page would look like a pile of tags instead of something a person could read. This file fixes that by fetching Confluence records, shaping them into consistent fields, and converting page bodies into plain prose.

The connector first asks Atlassian which Confluence sites the current authorization grant can reach. A single grant can cover more than one site, so each stream is read once per site. For each site, it calls the right Confluence API path and walks through results in small pages. Some streams are incremental, meaning the sync remembers a last-seen timestamp and only keeps records newer than that. Confluence does not support a true “give me changes since then” filter for these calls, so the connector still reads through pages and filters locally.

It also protects the rest of the system from two common problems. First, if access is denied because the grant lacks permission, it marks that stream as skipped instead of crashing the whole sync. Second, it prefixes record IDs with the site ID, so pages from different Confluence sites do not accidentally share the same identity. Finally, its small HTML text extractor strips tags while keeping the words and sensible line breaks.

#### Function details

##### `_body_text`  (lines 115–117)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the readable body field inside a Confluence record. It prefers Confluence’s storage-format body and falls back to the view body when needed.

**Data flow**: It receives one record as a dictionary-like object. It looks for text at body.storage.value, then at body.view.value. If it finds a non-empty string, it returns that string; otherwise it returns nothing.

**Call relations**: During record shaping, ConfluenceConnector.flatten calls this helper for pages, blog posts, and comments so those records get a simple body field before later rendering turns that body into recallable text.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 126–152)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Confluence stream, such as pages or spaces, across every Confluence site available to the current grant. It yields batches of records for the sync engine to consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It chooses the API path for that stream, asks which sites are accessible, reads paged results from each site, adds site context like cloud_id and site_url to each batch, and yields those batches onward. If Confluence refuses access with an authorization error, it turns that into a skipped stream rather than a failed run.

**Call relations**: The source-sync framework calls this when it needs records for a stream. This method asks _sites for the list of Confluence sites, hands each site-specific API path to _offset_results for paging, wraps returned records with with_context, and raises StreamSkipped when the grant cannot read a stream.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 154–158)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Asks Atlassian which Confluence sites the current authorization grant can access. This matters because Confluence API calls must be made under a specific site identifier.

**Data flow**: It receives an HTTP client. It calls Atlassian’s accessible-resources endpoint, reads the JSON response if there is content, normalizes it into a list, and returns that list of site records.

**Call relations**: ConfluenceConnector.paginate calls this before reading any stream. The returned site IDs become the cloud_id values used to build site-specific Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 160–184)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through a Confluence collection one page of API results at a time. It also applies the saved cursor check for streams that only want newer records.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the field to compare against that cursor. It repeatedly requests results with start and limit values, pulls the result records out of the response, filters out old records when a cursor is present, yields non-empty batches, and stops when there are no records to keep or Confluence does not provide a next-page link.

**Call relations**: ConfluenceConnector.paginate delegates the detailed paging work to this method for each site and stream. This method uses records_at to find the response’s results list and get_path to read nested cursor fields and next-page links.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 186–234)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Confluence API records into a more consistent shape for syncing and searching. It gives important fields clear names, adds readable body text, builds useful URLs, and prevents record ID collisions across sites.

**Data flow**: It receives one raw record and the stream it belongs to. Depending on the stream, it copies the record and adds fields such as title, kind, url, body, author, created_at, updated_at, or parent_external_id. It prefixes most primary keys with the site cloud_id, and it lifts nested cursor values like version.createdAt into a flat field so watermark tracking can compare them easily. It returns the shaped record.

**Call relations**: After pagination produces records, the connector framework uses this method to prepare each record for storage and later rendering. It calls _body_text to extract page-like body content and get_path to safely read nested Confluence fields.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 236–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Builds the human-readable text that will be stored for recall. For page-like Confluence content, it strips XHTML tags so the result reads like the actual page instead of raw markup.

**Data flow**: It receives a flattened record and its stream description. For pages, blog posts, and comments, it reads a title and converts the body through _StorageTextExtractor. For spaces, it reads the name or key and extracts text from the description. For other streams, it leaves rendering to the base connector. It returns a title plus a Markdown-like text block with a heading and body.

**Call relations**: The connector framework calls this when it needs searchable text for a synced record. It uses _str to safely turn possible titles into strings, get_path to read nested descriptions, and _StorageTextExtractor.extract to convert Confluence XHTML into plain text.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 261–263)

```
def __init__(self) -> None
```

**Purpose**: Sets up a small HTML parser that collects only the readable text pieces from Confluence’s storage-format XHTML.

**Data flow**: It receives no outside data beyond the new object being created. It initializes the underlying HTML parser with automatic character unescaping and creates an empty list where text fragments and line breaks will be collected. The result is a ready-to-use parser instance.

**Call relations**: _StorageTextExtractor.extract creates this parser whenever Confluence body text needs conversion. The parser’s later callbacks fill the internal parts list as the HTML is read.


##### `_StorageTextExtractor.extract`  (lines 266–271)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts a raw Confluence XHTML string into plain readable text. It is the main entry point for the text extractor.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise it creates a parser, feeds the raw XHTML into it, asks the parser to clean up the collected text, and returns that final plain-text result.

**Call relations**: ConfluenceConnector.render uses this when preparing pages, blog posts, comments, and space descriptions for recall. The method drives the parser, which then calls handle_data, handle_starttag, and handle_endtag as it reads the XHTML.


##### `_StorageTextExtractor.handle_data`  (lines 273–274)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Keeps the actual words found between HTML tags. This is where visible page text is collected.

**Data flow**: It receives a piece of text from the parser. It appends that text to the parser’s internal list. It does not return a value; it changes the parser’s collected content.

**Call relations**: The HTML parser calls this automatically while _StorageTextExtractor.extract feeds it XHTML. The collected text is later joined and cleaned by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 276–278)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag begins. This helps paragraphs, headings, table cells, and list items not run together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the internal parts list. Attributes are ignored because the goal is readable text, not formatting or metadata.

**Call relations**: The HTML parser calls this automatically during extraction. Its newline markers are later cleaned up by _StorageTextExtractor._text so the final output has sensible line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 280–282)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag ends. This gives the final plain text natural separation after paragraphs, headings, rows, and similar blocks.

**Data flow**: It receives the tag name. If the tag is a known block tag, it appends a newline marker to the internal parts list. It changes the parser’s collected content and returns nothing.

**Call relations**: The HTML parser calls this automatically while _StorageTextExtractor.extract reads the XHTML. Together with handle_starttag, it creates the raw line boundaries that _text later tidies.


##### `_StorageTextExtractor._text`  (lines 284–287)

```
def _text(self) -> str
```

**Purpose**: Cleans the collected text fragments into the final readable string. It removes extra spacing and drops empty lines.

**Data flow**: It takes the parser’s internal list of text pieces and newline markers, joins them, splits that joined text into lines, normalizes repeated whitespace inside each line, removes blank lines, trims the result, and returns the finished plain text.

**Call relations**: _StorageTextExtractor.extract calls this after the raw XHTML has been fully parsed. It is the final cleanup step before ConfluenceConnector.render receives usable prose.


##### `_str`  (lines 290–291)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only when it is already a string. It prevents non-text values from being treated as titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this when choosing titles for records. That lets rendering avoid accidental titles such as numbers, objects, or missing values.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/notion.py`

`io_transport` · `source sync and record rendering`

Notion stores human writing in a shape that is convenient for its API, not for recall or search. A page title may be hidden inside a property, a paragraph may be inside a typed block, and comments use rich-text pieces. Without this file, synced Notion records would mostly look like raw JSON, which is like saving a book as a pile of printing instructions instead of readable pages.

The main class, NotionConnector, is a read-only connector for the Notion web API. It declares the Notion streams this system knows about: users, pages, data sources, comments, and blocks. During a sync, it creates an HTTP client with the required Notion-Version header, then chooses the right reading path for each stream.

Pages and data sources are found through Notion search. Blocks are gathered by walking through each page’s block tree, but only to a safe depth and not into child pages or databases, because those are separate records. Comments are fetched for each page. Users are fetched as one flat collection. For incremental syncs, the connector compares Notion timestamps with the stored checkpoint, so it only yields newer records where possible.

The render step is just as important as the fetching. It extracts titles, property text, block text, comment bodies, and user names or emails into plain prose that people can actually search and remember.

#### Function details

##### `NotionConnector._make_client`  (lines 82–85)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Notion and adds the Notion API version header that Notion requires. Someone would use it indirectly when starting a Notion sync.

**Data flow**: It receives the base API address and a credential object supplied by the surrounding auth system. It asks the parent REST connector to build the normal client, adds the Notion-Version header, and returns the ready-to-use client. The credential is used by the parent setup; this function only adds the Notion-specific header.

**Call relations**: This is part of the connector setup before any Notion requests are made. After it returns the client, the rest of the connector methods use that client to fetch users, pages, blocks, comments, and data sources.


##### `NotionConnector.paginate`  (lines 87–117)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for reading a Notion stream. Given a stream name, it chooses the correct Notion API route and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor, which is a timestamp checkpoint from a previous run. It checks the stream name, calls the matching helper, and passes each batch of Notion records onward. If Notion replies with 401 or 403, meaning unauthorized or forbidden, it turns that into StreamSkipped so the sync records a skipped stream rather than a full failure.

**Call relations**: When the source runtime asks for records from a Notion stream, this function decides whether to call _collection, _search, _comments, or _blocks. If the stream is unknown, or access is refused, it reports that cleanly through StreamSkipped.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 119–142)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Notion for pages or data sources and returns them in time order. It is used when the connector needs top-level Notion objects that can be found through the /search API.

**Data flow**: It receives the HTTP client, the kind of Notion object to search for, and an optional timestamp cursor. It sends repeated POST requests to /search with a page size, object filter, and ascending last-edited sort. For each response, it takes the results list, drops records older than or equal to the cursor when one is present, yields any remaining records, and follows Notion’s next_cursor until there are no more pages.

**Call relations**: paginate calls this directly for pages and data_sources. _blocks and _comments also call it to find all pages first, because blocks and comments are fetched by page.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 144–154)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the block content that makes up the body of each Notion page. It exists because a page’s readable writing often lives in child blocks, not in the page record itself.

**Data flow**: It receives the HTTP client and an optional timestamp cursor. It first searches for all pages, ignoring the cursor at that search stage so it can inspect every page’s block tree. For each page with a valid ID, it asks _block_children to walk that page’s children and yields the block batches that come back.

**Call relations**: paginate calls this when the blocks stream is requested. This function starts from pages found by _search, then hands each page ID to _block_children, which does the actual recursive walk through the block tree.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 156–175)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through a Notion block’s children and yields the blocks that are new enough for the current sync. It is how the connector captures nested page body text, such as lists inside toggles or paragraphs under headings.

**Data flow**: It receives the HTTP client, a block ID, the current nesting depth, and an optional timestamp cursor. If the walk is too deep, it stops to avoid runaway recursion. Otherwise it fetches that block’s children, filters out blocks that are not newer than the cursor, yields the filtered batch, and then recursively visits child blocks when they have children and are safe to descend into.

**Call relations**: _blocks calls this for each page ID. Inside the walk, this function calls _collection to fetch each page of child blocks. It calls itself again for nested children, but deliberately does not descend into child pages, child databases, or AI blocks, because those should not be folded into the parent block walk.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 177–193)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches comments attached to Notion pages. It lets the sync include discussion text, not just page body content.

**Data flow**: It receives the HTTP client and an optional timestamp cursor. It searches for all pages, takes each valid page ID, requests comments for that page, filters out comments whose created_time is not newer than the cursor, and yields any remaining comment batches.

**Call relations**: paginate calls this for the comments stream. Like _blocks, it starts by using _search to find pages, then uses _collection to page through the comments endpoint for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 195–209)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared reader for Notion endpoints that return a paged list under a results field. It hides the repeated work of following Notion’s pagination cursors.

**Data flow**: It receives the HTTP client, an API path, and optional query parameters. It asks the parent REST connector to fetch cursor-based pages using Notion’s names for the record list, next cursor, cursor parameter, page-size parameter, and page size. It yields each page of records as a list of dictionaries.

**Call relations**: paginate uses this directly for users. _block_children uses it for block children, and _comments uses it for comments. It is the common low-level path for GET-style Notion collection endpoints.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 211–233)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a raw Notion API record into a human-readable title and body. It matters because Notion’s useful text is often buried inside rich-text arrays and typed fields.

**Data flow**: It receives one record and the stream it came from. Depending on the stream, it extracts the right kind of text: page title and properties, data source title and description, block body text, comment rich text, or user name and email. It then builds a simple Markdown-like heading and returns both the title and the full rendered text.

**Call relations**: The sync system calls this when it needs the stored, recallable text for a Notion record. It delegates the stream-specific extraction to helpers such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text. For unknown streams, it falls back to the parent connector’s rendering.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 236–237)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only when it already is one. It prevents accidental text like None or complex objects from appearing in rendered output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: render and the helper functions use this whenever they expect a plain string but the Notion API may return something else. _block_text, _property_text, and _user_text rely on it to keep rendered prose clean.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 240–248)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts readable text from Notion’s rich-text format. Rich text is a list of small pieces, and this function joins each piece’s plain_text into normal writing.

**Data flow**: It receives a value that should be a list of rich-text parts. If it is not a list, it returns an empty string. If it is a list, it keeps the parts that are dictionaries with a string plain_text field, joins those strings together, trims extra space at the ends, and returns the result.

**Call relations**: render uses this for comments and data source fields. _page_title, _property_text, and _block_text use it whenever they need to turn Notion rich-text fields into prose.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 251–260)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the visible title of a Notion page. In Notion, the title is stored as a special property rather than as a simple top-level field.

**Data flow**: It receives a page record. It looks inside the page’s properties dictionary, searches for the property whose type is title, extracts that property’s rich text, and returns the first non-empty title it finds. If the expected structure is missing, it returns an empty string.

**Call relations**: render calls this when rendering page records. This helper uses _rich_text_text to turn the title’s rich-text pieces into one readable string.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 263–272)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s visible properties into simple lines of text. It helps preserve useful page metadata such as status, dates, people, and labels.

**Data flow**: It receives a page record. It reads the properties dictionary, asks _property_text to extract readable text for each property, and builds lines in the form name: value for properties that have text. It returns all those lines joined with newline characters.

**Call relations**: render calls this when rendering page records. This function is the bridge between the whole page and _property_text, which knows how to read each individual property type.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 275–294)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from one Notion property. It understands the common property kinds that people use, such as titles, select fields, people, dates, numbers, links, emails, phone numbers, and checkboxes.

**Data flow**: It receives one property dictionary. It checks the property’s type, pulls the matching value field, and converts that value into text in a type-aware way. Rich-text fields are joined, select-like fields use their names, list-like fields are comma-separated, dates use the start date, and simple values are stringified when present. Unknown property types return an empty string.

**Call relations**: _properties_text calls this for each page property. It uses _rich_text_text for rich text and _str for safe string extraction.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 297–307)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from a Notion block, such as a paragraph, heading, list item, to-do item, code block, or child page title. Blocks are the building pieces of a Notion page body.

**Data flow**: It receives one block record. It looks up the block’s type, then reads the type-specific content. For child pages and child databases it returns the title. For normal text-like blocks it joins the rich-text content. For to-do blocks it also prefixes the text with [x] or [ ] to show whether the task is checked.

**Call relations**: render calls this for records in the blocks stream. It uses _rich_text_text to read normal block text and _str to safely read child page or database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 310–313)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This renders a Notion user as simple identifying text. It includes the user’s name and, when available, their email address.

**Data flow**: It receives a user record. It reads the top-level name and the nested person.email field, keeps only real strings, joins the available pieces with a newline, and returns that text.

**Call relations**: render calls this for records in the users stream. It uses _str to avoid putting missing or non-string values into the rendered user text.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Support platforms
Helpdesk and customer-support connectors that import tickets, users, articles, forums, and community content for recall.

### `extensions/sources/ufo_ext_sources/providers/freshdesk.py`

`io_transport` · `source sync`

Freshdesk exposes many kinds of data through its web API, but not all of them are fetched in the same way. This file is the adapter that knows those rules. Without it, the system might know that Freshdesk exists, but it would not know which API paths to call, how to sign in, how to page through long result lists, or how to walk nested structures such as “category → folder → article.”

The file first defines the Freshdesk streams: these are named collections of records, such as tickets, companies, conversations, solution articles, and discussion comments. A stream is like a labeled conveyor belt of one kind of Freshdesk data.

The main class, `FreshdeskConnector`, builds an HTTP client for Freshdesk using HTTP Basic authentication, where the API key is used as the username. It then decides how each stream should be read. Simple streams use Freshdesk’s standard “next page” links. Tickets use page numbers and can be filtered by an update cursor, so syncs can resume from a previous point in time. Conversations are fetched by first reading tickets, then asking Freshdesk for each ticket’s conversations. Knowledge-base and forum data require walking parent-child trees.

A notable safety behavior is that if Freshdesk replies with “unauthorized” or “forbidden,” this connector skips that stream with a clear message instead of treating the whole sync as a mysterious failure.

#### Function details

##### `_stream`  (lines 57–71)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a `StreamSpec`, which is the system’s description of one Freshdesk data stream. It keeps the stream list compact and consistent.

**Data flow**: It takes a stream name plus optional details such as the Freshdesk object name, primary key, cursor field, and whether the stream is a main canonical stream. It fills in sensible defaults, then returns a `StreamSpec` object that the connector later uses to know what to read and how to identify records.

**Call relations**: This helper is used while the file is being loaded to build `FRESHDESK_STREAMS`. Those stream descriptions are then attached to `FreshdeskConnector`, so the rest of the source-sync system can ask this connector what Freshdesk data is available.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector.record_identity`  (lines 112–115)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function decides the stable identity for a Freshdesk record. It gives the special `settings` stream one fixed identity because Freshdesk helpdesk settings are a single configuration page rather than a normal list of many records.

**Data flow**: It receives one record and the stream it came from. If the stream is `settings`, it returns the fixed key `helpdesk`; otherwise it lets the base connector use its normal identity rules, usually based on the stream’s primary key.

**Call relations**: The broader sync machinery calls this when it needs to name or deduplicate records. This override only steps in for `settings`; every other stream follows the standard behavior inherited from `RestConnector`.


##### `FreshdeskConnector.record_ref`  (lines 117–121)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function chooses a human-friendly reference value for a record. For Freshdesk settings, it uses the primary language when available, which is more meaningful than a made-up settings ID.

**Data flow**: It receives a record and its stream. For ordinary streams, it delegates to the base connector. For the `settings` stream, it looks for `primary_language`; if that value is text or a number, it returns it as text, otherwise it returns nothing.

**Call relations**: The sync system uses record references when it wants a readable label for stored or indexed records. This function fits beside `record_identity`: one gives the settings page a stable identity, and the other gives it a useful label.


##### `FreshdeskConnector._make_client`  (lines 123–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Freshdesk. It applies timeouts, JSON headers, the tenant-specific base URL, and the correct authentication method.

**Data flow**: It receives a Freshdesk base URL and a resolved credential. It trims trailing slashes from the URL, prepares headers, and sets connection and read time limits. If the credential already contains a custom transport, it uses that unchanged. Otherwise, if it contains an API key, it sends that key using HTTP Basic authentication with `X` as the dummy password. If there is no usable authentication information, it raises an error.

**Call relations**: The connector setup code calls this before any API pages are fetched. The HTTP client it returns is then passed into `paginate` and the lower-level pagination helpers, which use it for all Freshdesk requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 141–151)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: This function builds the query parameters Freshdesk expects when reading ticket pages. It centralizes the ticket-specific options, including page size, page number, sort order, included extra fields, and optional incremental syncing.

**Data flow**: It receives an optional cursor and a page number. It creates a parameter dictionary asking for up to 100 tickets, sorted by `updated_at` from oldest to newest, including extra ticket details. If a cursor is present, it adds `updated_since` so Freshdesk only returns tickets updated after that point.

**Call relations**: `_paginate_tickets` calls this each time it asks Freshdesk for the next ticket page. Keeping the parameter-building here makes the ticket pagination loop easier to understand and keeps all ticket API options in one place.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 153–180)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading records from one Freshdesk stream. Given a stream name and an optional cursor, it yields pages of records using the right Freshdesk pagination style.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It first checks whether the stream needs special treatment, such as tickets, conversations, or nested article/forum trees. If not, it looks up the simple API path. Settings are fetched as one single record; other simple streams are read by following Freshdesk’s `next` links. It yields lists of records as they arrive. If Freshdesk refuses access with status 401 or 403, it turns that into a clear `StreamSkipped` result.

**Call relations**: The sync engine calls this whenever it wants records for a Freshdesk stream. `paginate` acts as the traffic director: it may hand work to `_special_pages`, `_paginate_link_header`, or the single-settings fetch path depending on the stream.

*Call graph*: calls 3 internal fn (__init__, _paginate_link_header, _special_pages).


##### `FreshdeskConnector._special_pages`  (lines 182–223)

```
def _special_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]] | None
```

**Purpose**: This function decides whether a stream needs a custom fetching plan instead of the simple one-page-after-another API path. It covers tickets, conversations, two-level parent-child streams, and three-level knowledge-base article trees.

**Data flow**: It receives the HTTP client, a stream name, and an optional cursor. For known special stream names, it returns an asynchronous page iterator from the matching helper. For example, tickets go to `_paginate_tickets`, conversations go to `_paginate_conversations`, and solution articles go to `_paginate_three_level`. If the stream is not special, it returns nothing.

**Call relations**: `paginate` calls this before trying the simple-path table. This function is the connector’s routing map for Freshdesk endpoints whose data is nested or paged in a nonstandard way.

*Call graph*: calls 4 internal fn (_paginate_conversations, _paginate_three_level, _paginate_tickets, _paginate_two_level); called by 1 (paginate).


##### `FreshdeskConnector._paginate_link_header`  (lines 225–232)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Freshdesk endpoints that use standard `Link` headers for pagination. A `Link` header is an HTTP response header that tells the client where the next page is.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It starts at that path with a page size of 100, follows each `next` link supplied by Freshdesk, and yields each page of records as a list.

**Call relations**: `paginate` uses this for most simple Freshdesk streams. The nested pagination helpers also rely on it when fetching parent pages and child pages, so it is the shared “follow the next-page signs” tool.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 234–251)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Freshdesk tickets, which use page-number pagination instead of `next` links. It also supports incremental syncing by asking only for tickets updated since a cursor time.

**Data flow**: It starts at page 1 and repeatedly builds ticket query parameters with `_build_tickets_params`. It fetches each ticket page, extracts the records, yields them, and stops when Freshdesk returns no records, returns fewer than the maximum page size, or reaches Freshdesk’s 300-page ceiling.

**Call relations**: `_special_pages` chooses this helper for the `tickets` stream. `_paginate_conversations` also uses it first, because conversations are fetched by walking through the tickets that match the current cursor.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, _special_pages).


##### `FreshdeskConnector._paginate_conversations`  (lines 253–269)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ticket conversations by first finding tickets, then fetching the conversation list for each ticket. It exists because conversations are not read as one flat global stream in the same way as simple Freshdesk objects.

**Data flow**: It receives the HTTP client and an optional cursor. It asks `_paginate_tickets` for ticket pages allowed by that cursor. For each ticket with an ID, it calls the ticket-specific conversations endpoint, follows its paginated results, and stamps each conversation with the ticket ID if it is not already present. It yields conversation pages as it goes.

**Call relations**: `_special_pages` returns this helper for the `conversations` stream. Internally it combines `_paginate_tickets` and `_paginate_link_header`: tickets provide the parent IDs, and link-header pagination reads each ticket’s child conversations.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_two_level`  (lines 271–282)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Freshdesk data arranged in a two-level parent-child shape, such as folders to responses or categories to forums. It is a reusable walker for “get parents, then get children for each parent.”

**Data flow**: It receives a parent API path and a child-path template containing an `{id}` placeholder. It reads parent pages with `_paginate_link_header`, pulls each parent’s ID, fills that ID into the child path, then reads and yields the child pages. Parents without IDs are skipped because their child endpoint cannot be built.

**Call relations**: `_special_pages` uses this helper for several nested streams, including canned responses, solution folders, discussion forums, discussion topics, and discussion comments. It hands all actual page-following work to `_paginate_link_header`.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_three_level`  (lines 284–307)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Freshdesk data arranged in a three-level tree. In this file it is used for solution articles, which live under solution categories and folders.

**Data flow**: It receives paths for the root level, middle level, and leaf level. It reads root records, takes each root ID to fetch middle records, then takes each middle ID to fetch leaf records. It yields the leaf pages, skipping any category or folder that does not have an ID.

**Call relations**: `_special_pages` chooses this helper for the `solution_articles` stream. Like the two-level walker, it relies on `_paginate_link_header` at each layer, but it adds one more step so the connector can reach articles buried inside Freshdesk’s knowledge-base tree.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


### `extensions/sources/ufo_ext_sources/providers/zendesk.py`

`io_transport` · `source sync`

Zendesk has many different API endpoints, and they do not all page through results in the same way. This file is the adapter that hides those differences. To the rest of the system, Zendesk looks like a set of named streams such as tickets, users, ticket comments, articles, and posts. Inside this file, each stream is mapped to the right Zendesk API path and the right way to keep asking for more pages.

The main class, ZendeskConnector, builds on a shared RestConnector, which is the project’s general tool for reading from HTTP APIs. Some Zendesk objects use an incremental export API, meaning “give me everything changed since this time.” That is important for large objects like tickets and users, because a full scan every time would be slow and wasteful. Other objects use ordinary page-by-page links. A few streams need special treatment: ticket comments are buried inside ticket event records, so this file pulls them out into their own rows; user identities require first reading users and then asking Zendesk for each user’s identities.

The file also makes Zendesk permission failures clearer. If Zendesk refuses a stream with a 401 or 403 status, the connector marks that stream as skipped instead of crashing the whole sync. Like a mail sorter, it knows which chute each kind of Zendesk data belongs in and keeps passing batches onward until there are no more pages.

#### Function details

##### `_stream`  (lines 59–77)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a StreamSpec, which is the project’s small description card for one Zendesk data stream. It records things like the stream name, the Zendesk object it reads from, and which fields mark creation or update time.

**Data flow**: It receives a stream name plus optional details such as the source API object, primary key, cursor field, and whether the stream is considered canonical. It fills in sensible defaults when values are not supplied, then returns a StreamSpec that can be placed in the connector’s stream list.

**Call relations**: This helper is used while the file is loaded to build ZENDESK_STREAMS. It hands each finished StreamSpec to the connector configuration so later sync code knows which Zendesk streams exist and how each one should be read.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 143–168)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: Adds useful information from sideloaded Zendesk data onto the main records. In this file, it is used for tickets so requester, submitter, and assignee email addresses can be placed directly on each ticket record.

**Data flow**: It receives a list of main records, the full API page returned by Zendesk, and instructions for how to match related records. It builds a quick lookup table from the sideloaded arrays, finds related items by ID, and writes selected values such as email addresses back into the main records. It changes the records in place and returns nothing.

**Call relations**: ZendeskConnector._paginate_incremental_cursor calls this after downloading a page for streams that request sideloaded data. The helper enriches the batch before that paginator yields the records onward to the broader sync process.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 178–179)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Figures out which field inside a Zendesk response contains the actual list of records for a stream. Most streams use their own name, but some Zendesk endpoints use different words such as attributes, audits, or policies.

**Data flow**: It receives a StreamSpec and checks whether that stream has a known response-field override. It returns the override when one exists, otherwise it returns the stream’s own name.

**Call relations**: ZendeskConnector._paginate_default calls this before reading ordinary paged endpoints. This lets the default paginator pull records from the correct part of each Zendesk response without hard-coding every endpoint inside the loop.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 182–194)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: Converts the project’s saved checkpoint value into the Unix timestamp format Zendesk expects. A Unix timestamp is a count of seconds since January 1, 1970.

**Data flow**: It receives a cursor value, which may be missing, already numeric text, or an ISO-style date string. Missing or unreadable values become 0, numeric text becomes an integer, and valid date text is parsed and converted into seconds. The result is an integer start time for Zendesk incremental APIs.

**Call relations**: The incremental paginators call this before building their first Zendesk URL. ZendeskConnector._paginate_incremental_cursor, ZendeskConnector._paginate_ticket_comments, and ZendeskConnector._paginate_user_identities all use it so they can ask Zendesk for data changed since the last checkpoint.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 197–206)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: Turns Zendesk’s next-page URL into a path that the connector can request. This keeps pagination working whether Zendesk returns a full URL or a path-like link.

**Data flow**: It receives a next-page URL or nothing. If there is no usable path, it returns nothing. Otherwise it extracts the path and query string, such as `/api/v2/users.json?page=2`, and returns that smaller request path.

**Call relations**: All paginator methods use this when Zendesk says there is another page. It is the small bridge between Zendesk’s paging links and the connector’s internal `_get` calls.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 208–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a requested Zendesk stream and yields batches of records. It is the main doorway the sync runner uses to read Zendesk data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name and routes the work to the special ticket-comments paginator, the user-identities paginator, the incremental cursor paginator, or the default paginator. It yields each batch it receives. If Zendesk rejects access with 401 or 403, it raises StreamSkipped so that one unavailable stream can be skipped cleanly.

**Call relations**: The wider source-sync framework calls this method when it wants records for a Zendesk stream. This method then delegates to ZendeskConnector._paginate_ticket_comments, ZendeskConnector._paginate_user_identities, ZendeskConnector._paginate_incremental_cursor, or ZendeskConnector._paginate_default, depending on what kind of Zendesk endpoint is needed.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 234–255)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads high-volume Zendesk streams through Zendesk’s cursor-based incremental export API. This is used for large changing datasets where the connector should resume from a saved time rather than start over.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It converts the cursor to a Unix start time, builds the first incremental export URL, downloads each page, optionally enriches ticket records with sideloaded user emails, yields non-empty record batches, and follows Zendesk’s after_url or next_page until Zendesk says the stream has ended.

**Call relations**: ZendeskConnector.paginate calls this for streams listed as incremental cursor streams. Inside the loop, it relies on ZendeskConnector._cursor_to_unix to prepare the starting point, _apply_sideload to enrich ticket pages when needed, and ZendeskConnector._next_page_path to continue from one Zendesk page to the next.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 257–267)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Zendesk streams that use ordinary page-by-page API results. This is the fallback path for streams that do not need special incremental export behavior.

**Data flow**: It receives an HTTP client and a stream description. It builds a standard `/api/v2/...json` URL, asks Zendesk for a page, extracts the list of records from the right response field, yields the records when present, and follows the next_page link until there are no more pages.

**Call relations**: ZendeskConnector.paginate calls this when a stream does not have a special reader. This method uses ZendeskConnector._data_field to know where records live in the response and ZendeskConnector._next_page_path to keep moving through Zendesk’s pagination links.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 269–301)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a separate ticket-comments stream from Zendesk ticket events. Zendesk exposes comments as child events inside a larger event feed, so this method pulls those comments out into plain comment records.

**Data flow**: It receives an HTTP client and an optional cursor. It asks Zendesk’s ticket-events incremental API for events since the cursor time, looks through each event’s child_events, keeps only child events whose type is Comment, copies each comment, adds the parent ticket_id, normalizes numeric creation times into readable UTC date strings, and yields batches of comments. It follows Zendesk’s paging links until the event stream ends.

**Call relations**: ZendeskConnector.paginate calls this when the requested stream is ticket_comments. It uses ZendeskConnector._cursor_to_unix to start from the right checkpoint and ZendeskConnector._next_page_path to continue through the event feed.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 303–328)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads identity records for users, such as email or login identities, even though Zendesk requires a separate identities request for each user. It turns a two-step API pattern into one stream of identity records.

**Data flow**: It receives an HTTP client and an optional cursor. First it pages through incrementally changed users since that cursor. For each user with an ID, it requests that user’s identities, yields any identity records found, and follows identity pages if there are several. Then it continues to the next page of users until Zendesk says the user stream is complete.

**Call relations**: ZendeskConnector.paginate calls this for the users_identities stream. It uses ZendeskConnector._cursor_to_unix to begin with changed users only, and ZendeskConnector._next_page_path both for user pages and for per-user identity pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).


### Developer collaboration
The GitHub provider imports repositories, issues, commits, releases, users, and comments as searchable development context.

### `extensions/sources/ufo_ext_sources/providers/github.py`

`io_transport` · `source sync`

This connector solves a practical problem: GitHub data is spread across many API endpoints, and most of it only makes sense inside a repository or organization. Without this file, the system would not know which GitHub URLs to call, how to page through long result lists, how to resume a sync safely, or how to stop records from different repositories overwriting each other.

The file starts by declaring the GitHub streams the system knows about, such as issues, pull requests, commits, users, and repositories. A stream is a kind of data the sync can read. It then maps runnable streams to GitHub API paths.

The main class, GitHubConnector, builds an authenticated GitHub HTTP client, discovers the organizations available to the token, lists their repositories, and then reads repository-specific streams one repository at a time. Think of it like a mail carrier who first finds every building, then visits every apartment in each building, stamping each letter with its building and apartment so letters with the same room number do not get mixed up.

That stamping is important. GitHub has many IDs that are only unique inside one repository, such as branch names like main or tag names like v1.0. This connector adds the repository or organization context before records become pages. It also has careful resume behavior for streams ordered by time, so interrupted syncs can continue without missing new GitHub activity.

#### Function details

##### `_stream`  (lines 88–108)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Creates a StreamSpec, which is the system's description of one kind of GitHub data to sync. It gives each stream its name, key field, time field, and ordering rules in one compact place.

**Data flow**: It receives stream settings such as the stream name, primary key, cursor field, and ordering. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses to decide how to fetch and resume that stream.

**Call relations**: This helper is used while the module builds the ALL_STREAMS catalog. It hands the completed stream descriptions to the rest of the connector, which later filters them and uses them during pagination and rendering.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 213–216)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns only the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog compatibility but do not yet have an API path wired up.

**Data flow**: It reads the full stream list from the class and checks each stream name against the path table. It returns a shorter list containing only streams with known GitHub API routes.

**Call relations**: The source framework calls this when it asks the connector what it can run. This method acts as the gate between the broad catalog and the implemented fetching paths.


##### `GitHubConnector._make_client`  (lines 218–222)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to call GitHub and adds the GitHub-specific headers GitHub expects. These headers say which API format and version the connector wants.

**Data flow**: It receives a base URL and a resolved credential. It asks the parent RestConnector to create the authenticated client, adds Accept and API-version headers, and returns the ready-to-use client.

**Call relations**: The broader source framework relies on this during setup before any GitHub request is made. After this, all pagination and lookup methods use the configured client.


##### `GitHubConnector.flatten`  (lines 224–273)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares one GitHub record before it becomes a recallable page, especially by adding repository or organization context to its ID. This prevents records with the same local ID in different repositories from colliding.

**Data flow**: It receives a raw GitHub record and the stream description. It may reshape special records, such as flattening stargazer user data or removing large nested repository objects from pull requests. Then it finds the partition field, reads the record's primary key, prefixes that key with the repo or org name when needed, and returns the shaped record.

**Call relations**: This runs after records have been fetched and stamped with context by the pagination flow. It calls _partition_field to know whether the stream is repo- or org-scoped, and get_path when the key is nested inside the record.

*Call graph*: calls 1 internal fn (_partition_field); 1 external calls (get_path).


##### `GitHubConnector.record_identity`  (lines 275–282)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Finds the stable identity for a record, with special handling for contributor activity records. Contributor activity uses a nested author ID, so the normal primary-key lookup is not enough.

**Data flow**: It receives a record and its stream. For most streams, it delegates to the parent connector. For contributor activity, it reads the repository stamp and the nested author ID, combines them, and returns that combined identity, or returns nothing if either piece is missing.

**Call relations**: The sync framework uses this when deciding whether a row has already been seen. It uses get_path for the nested author ID and keeps contributor records distinct per repository.

*Call graph*: 1 external calls (get_path).


##### `GitHubConnector.render`  (lines 284–289)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a record into page content and title data, with one special rule for pull requests. Pull request update timestamps are kept as metadata rather than included in the visible page body.

**Data flow**: It receives a record and stream. For normal streams, it lets the parent renderer do the work. For pull requests, it removes the updated-at field from the content copy, then sends that cleaned content to the parent renderer.

**Call relations**: This is called when fetched records are converted into pages. It fits after flattening and before the record is stored or indexed by the rest of the system.


##### `GitHubConnector.paginate_source`  (lines 291–302)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the source framework with a stream of GitHub pages, while accepting an extra backfill floor used by time-ordered repository streams. A backfill floor is the oldest time the sync should bother reading back to.

**Data flow**: It receives the HTTP client, stream, current cursor, user ID, and optional backfill cutoff. It passes the useful pieces into paginate and returns the async stream of pages that paginate produces.

**Call relations**: The source framework calls this as the main read seam. It immediately hands off to GitHubConnector.paginate, which chooses the correct path for the stream.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 304–338)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right GitHub fetching strategy for a stream. It decides whether to read organizations, repositories, one repository at a time, or a simple top-level endpoint.

**Data flow**: It receives a stream, cursor, and optional backfill cutoff. It looks up the stream's API path, prepares default page parameters, adds state=all for streams like issues and pull requests, then yields pages from the appropriate helper.

**Call relations**: GitHubConnector.paginate_source calls this during a sync. Depending on the path, it hands off to _repository_pages, _repo_stream_pages, _org_stream_pages, or _paginate_link_header.

*Call graph*: calls 4 internal fn (_org_stream_pages, _paginate_link_header, _repo_stream_pages, _repository_pages); called by 1 (paginate_source).


##### `GitHubConnector._repository_pages`  (lines 340–344)

```
async def _repository_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads repository records for every organization the credential can see. Each repository page is stamped with the organization it came from.

**Data flow**: It receives the HTTP client. It walks organization repository pages, adds org_login to every record in each page, and yields those stamped pages.

**Call relations**: GitHubConnector.paginate uses this for the repositories stream. It gets its raw pages from _iter_granted_org_repo_pages and uses with_context to add the organization context.

*Call graph*: calls 1 internal fn (_iter_granted_org_repo_pages); called by 1 (paginate); 1 external calls (with_context).


##### `GitHubConnector._repo_partitions`  (lines 346–348)

```
async def _repo_partitions(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the list of repository partitions that repo-scoped streams should visit. A partition here is one repository, written as owner/repo.

**Data flow**: It receives the HTTP client, reads accessible repositories through _iter_user_repos, combines each owner and repo name into a single string, and yields those strings one at a time.

**Call relations**: This supplies PartitionWalk inside _repo_stream_pages. PartitionWalk then asks for pages for each repository partition separately.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector._repo_stream_pages`  (lines 350–374)

```
async def _repo_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, cursor: str | None, backfill_after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs a repository-scoped stream across all discovered repositories while preserving safe resume behavior. It delegates the tricky cursor and partition walking to PartitionWalk.

**Data flow**: It receives the client, stream, API path, cursor, and optional backfill cutoff. It converts the cutoff into GitHub's timestamp format, creates a PartitionWalk with repository partitions and page-producing callbacks, then yields each StreamPage from that walk. It also closes the async generator when finished.

**Call relations**: GitHubConnector.paginate calls this for paths containing owner and repo placeholders. It wires _repo_partitions and _repo_pages into PartitionWalk so the SDK can track progress per repository.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, astimezone, partial).


##### `GitHubConnector._org_stream_pages`  (lines 376–394)

```
async def _org_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, params: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads organization-scoped streams, such as users or teams, once for each organization the token can access. For users, it optionally enriches simple member records with fuller public profile data.

**Data flow**: It receives the client, stream, path template, and request parameters. It loops through organization logins, fills the org into the path, paginates that endpoint, enriches user pages when needed, stamps records with org_login, and yields them. If one organization is gone or inaccessible, it skips that organization and continues.

**Call relations**: GitHubConnector.paginate calls this for paths containing an org placeholder. It depends on _iter_user_orgs for the organization list, _paginate_link_header for API pages, _enrich_users for user details, and with_context for stamping.

*Call graph*: calls 3 internal fn (_enrich_users, _iter_user_orgs, _paginate_link_header); called by 1 (paginate); 2 external calls (Semaphore, with_context).


##### `GitHubConnector._repo_pages`  (lines 396–467)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches one bounded slice of one repository stream. This is where GitHub query parameters and client-side filters are applied so the partition walker can resume without missing records.

**Data flow**: It receives a repo key, stream, path, and PartitionBound, which says the time window to read. It builds GitHub parameters such as since, until, sort, and direction; fetches pages; removes pull requests from the issues stream; filters newest-first streams when GitHub has no server-side time filter; computes the page's high and low cursor values; stamps records with repo_full_name; and yields WalkPage objects.

**Call relations**: PartitionWalk calls this through the callback created in _repo_stream_pages. It relies on _paginate_link_header to talk to GitHub, _cursor_bounds to report time spans, with_context to stamp repository context, and raises PartitionSkipped when a single repository cannot be read.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 469–477)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Yields accessible organization-owned repositories as owner/name pairs. It deliberately avoids GitHub's broad user repository endpoint because that could include personal, collaborator, archived, or forked repositories outside the intended grant scope.

**Data flow**: It receives the client, reads repository pages from granted organizations, extracts a reliable owner and repo name from each record, and yields only records whose identity can be understood.

**Call relations**: _repo_partitions calls this when repository-scoped streams need their work list. It uses _iter_granted_org_repo_pages for the raw repository pages and _repo_identity to parse each repository record.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (_repo_partitions).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 479–498)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Lists usable repositories for each organization visible to the credential. It skips archived repositories and forks because this connector is focused on active organization-owned repositories.

**Data flow**: It receives the client, loops through organizations, requests each organization's repositories, filters out archived and forked repos, and yields non-empty pages along with the org login. If an individual organization is forbidden or missing, it skips that org.

**Call relations**: _repository_pages uses this to produce the repositories stream, and _iter_user_repos uses it to discover repository partitions. It gets organizations from _iter_user_orgs and pages from _paginate_link_header.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, _repository_pages).


##### `GitHubConnector._iter_user_orgs`  (lines 500–521)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the GitHub organizations exposed by the credential. This is the root discovery step for almost every runnable stream.

**Data flow**: It receives the client, calls /user/orgs through the page helper, reads each organization's login, and yields valid logins. If GitHub refuses this root organization listing with a scope-related 403, it raises StreamSkipped so the run is recorded as skipped rather than broken.

**Call relations**: _iter_granted_org_repo_pages and _org_stream_pages both call this before they can fan out over organizations. It uses _paginate_link_header for the actual API walk and StreamSkipped to report a missing organization permission cleanly.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, _org_stream_pages).


##### `GitHubConnector._enrich_users`  (lines 523–543)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Replaces basic organization member records with fuller GitHub public user records when possible. This can add public profile details such as name or email.

**Data flow**: It receives a page of member records and a semaphore, which is a small traffic light that limits how many user lookups run at the same time. It launches one lookup task per member, waits for them all, and returns a new list containing enriched records where available and original records otherwise.

**Call relations**: _org_stream_pages calls this only for the users stream. It uses asyncio.gather to run the per-user helper concurrently while the semaphore keeps the number of simultaneous GitHub requests under control.

*Call graph*: called by 1 (_org_stream_pages); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 529–541)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Looks up one GitHub user by login and returns the fuller public user record if GitHub provides it. If the user cannot be found, it keeps the original member record.

**Data flow**: It receives one member record from the surrounding _enrich_users function. It reads the login, waits for permission from the semaphore, calls /users/{login}, parses the response as a dictionary, and returns either that dictionary or the original member.

**Call relations**: This helper is created and used inside _enrich_users. Many of these helpers run together, and their results are gathered back into one enriched page.


##### `GitHubConnector._paginate_link_header`  (lines 545–553)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through GitHub's standard paginated responses. GitHub uses Link headers to point to the next page, and this helper hides that repeated next-page work.

**Data flow**: It receives the client, path, and optional query parameters. It asks the parent paging helper to fetch each page using the configured page size and _parse_records, then yields each parsed list of records.

**Call relations**: This is the shared low-level pager used by paginate, _repo_pages, _org_stream_pages, _iter_user_orgs, and _iter_granted_org_repo_pages. Those higher-level methods decide what to fetch; this method repeatedly fetches the pages.

*Call graph*: called by 5 (_iter_granted_org_repo_pages, _iter_user_orgs, _org_stream_pages, _repo_pages, paginate).


##### `_partition_field`  (lines 556–564)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Figures out which context field should be present on records from a path. Repository paths need repo_full_name, organization paths need org_login, and top-level paths need neither.

**Data flow**: It receives a GitHub API path template. It checks whether the path contains repo or org placeholders and returns the matching context field name, or returns nothing for unpartitioned paths.

**Call relations**: GitHubConnector.flatten calls this before scoping a primary key. It lets flatten know whether a record must carry repository or organization context.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 567–571)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a GitHub HTTP response into a list of record dictionaries. It treats empty responses and non-list JSON bodies as no records.

**Data flow**: It receives an HTTP response. If there is no body, it returns an empty list. Otherwise it parses JSON and returns it only when the JSON is a list; anything else becomes an empty list.

**Call relations**: This is passed into the link-header paging helper through _paginate_link_header. It keeps the rest of the connector working with simple lists of records instead of raw HTTP responses.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 574–589)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts an owner and repository name from a GitHub repository record. It supports several shapes GitHub may return.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries full_name, then owner.login plus name, then fallback owner plus name. It returns an owner/repo tuple when it can, or nothing when the record lacks enough information.

**Call relations**: _iter_user_repos calls this while turning repository records into repository partitions. Its output becomes the owner/repo strings used by repo-scoped syncing.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 592–602)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page. A cursor is the timestamp-like field used to know where a sync can resume.

**Data flow**: It receives a page of records and a cursor field name. If there is no cursor field, it returns two empty values. Otherwise it reads that field from each record, including nested fields, keeps string values, and returns the maximum and minimum values.

**Call relations**: _repo_pages calls this after fetching each page. The high and low values are handed to WalkPage so PartitionWalk can update watermarks and decide when to stop or resume.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


##### `resource_url`  (lines 605–619)

```
def resource_url(url: str) -> str | None
```

**Purpose**: Normalizes a GitHub issue or pull request URL into one canonical web URL. This lets different-looking links to the same issue or pull request be treated as the same watched resource.

**Data flow**: It receives a URL string. It trims it, checks it against supported GitHub web and API URL patterns, lowercases the repository name, chooses either pull or issues, and returns the canonical https://github.com/owner/repo/path/number form. If the URL is not a specific issue or pull request, it returns nothing.

**Call relations**: This is a standalone helper for code that needs to narrow activity to a specific GitHub resource. It shares the module's regular-expression patterns with resource_aliases.


##### `resource_aliases`  (lines 622–638)

```
def resource_aliases(resource: str) -> tuple[str, ...]
```

**Purpose**: Returns the common GitHub URL fragments that can refer to the same issue or pull request. This helps match records that store web URLs and API URLs in different fields.

**Data flow**: It receives a canonical resource URL. If it matches the supported GitHub issue or pull request shape, it extracts the repository and number, then returns web and API-style aliases for both pull and issues paths. If it does not match, it returns an empty tuple.

**Call relations**: This is a standalone companion to resource_url. After a resource has been canonicalized, this supplies the alternate strings that GitHub records may contain for that same resource.


### Google Workspace
Google providers cover mailbox, calendar, documents, Drive files, Meet artifacts, and spreadsheets with incremental source-sync behavior.

### `extensions/sources/ufo_ext_sources/providers/gmail.py`

`io_transport` · `source sync`

Gmail does not hand over emails as simple rows of text. A message is a nested MIME tree, meaning the readable parts may be buried in separate plain-text or HTML sections, encoded in a web-safe form of base64. Without this file, synced Gmail messages would look like hard-to-read technical data instead of normal mail, and the system would not know how to keep up with Gmail’s change history.

The main class, GmailConnector, is the bridge between the system and Gmail’s REST API, which is a web interface for asking Gmail for data. On the first sync, it lists message IDs in a pinned backfill window, usually recent mail, then chooses a Gmail history ID to remember where it left off. On later syncs, it asks Gmail for changes since that history ID, collects newly added message IDs, and records deleted message IDs as tombstones so the rest of the system can remove them.

After it has IDs, it fetches each full message body, flattens useful fields such as sender, recipients, subject, labels, and body text, and renders the message as readable prose. If only HTML is present, it strips the tags and keeps the text. It also treats expired Gmail history cursors and missing permissions carefully, so old cursors trigger a clean resync and missing Gmail scope records a skipped stream rather than a broken run.

#### Function details

##### `GmailConnector.paginate_source`  (lines 94–104)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the entry point the broader source-sync system uses to ask Gmail for pages of message changes. It adds Gmail-specific support for a backfill cutoff date, then delegates the real work.

**Data flow**: It receives an HTTP client, a stream description, an optional saved cursor, an optional user ID, and an optional backfill date. It passes the relevant pieces into GmailConnector.paginate and returns the stream of pages that paginate produces.

**Call relations**: The source runner calls this when it wants Gmail data. This function is a thin adapter: it hands the request to GmailConnector.paginate so the rest of the Gmail-specific sync path can decide whether to backfill or read history changes.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 106–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for Gmail messages. It decides whether to do an initial backfill or a later change-history sync, fetches message bodies, and yields pages the rest of the system can write.

**Data flow**: It takes a Gmail API client, the stream to sync, an optional cursor, and an optional backfill date. With no cursor, it gathers message IDs from the backfill window; with a cursor, it asks Gmail for added and deleted messages since that point. It fetches full message records in chunks, then yields StreamPage objects containing new records, deletions, and the next cursor.

**Call relations**: GmailConnector.paginate_source hands control here. This function calls _backfill for first-time syncs, _history for incremental syncs, and _fetch_bodies to turn message IDs into full records. If Gmail says the grant lacks the needed read scope, it converts that into StreamSkipped so the larger run records a skip instead of a crash.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 2 external calls (__init__, refused_for_scope).


##### `GmailConnector._backfill`  (lines 145–170)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time listing of Gmail message IDs in the allowed historical window. It also chooses a starting history ID so future runs can switch from full listing to change tracking.

**Data flow**: It receives an HTTP client and an optional cutoff date. It first reads the mailbox profile’s current history ID as a safety floor, then lists message IDs from Gmail, adding an after: time filter when a cutoff exists. After all pages are read, it returns the collected message IDs and a seed cursor for the next run.

**Call relations**: GmailConnector.paginate calls this when there is no saved cursor. It asks _profile_history_id for the mailbox’s current history marker and then asks _seed_history_id to pick the best cursor after listing the backfill window.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 172–175)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail’s current mailbox history marker from the profile endpoint. That marker is used as a safe fallback cursor when the backfill window contains no messages.

**Data flow**: It receives an HTTP client, asks Gmail for the mailbox profile, and looks for a string historyId. It returns that history ID when present, otherwise it returns nothing.

**Call relations**: GmailConnector._backfill calls this before listing messages. Reading it early matters because it prevents messages delivered during an empty backfill scan from being accidentally skipped by the next history sync.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 177–201)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the cursor that future Gmail syncs should start from after a backfill. It prefers the newest listed message’s history ID, but falls back to the profile history ID if needed.

**Data flow**: It receives an HTTP client, the list of message IDs found during backfill, and a fallback floor history ID. If there are messages, it fetches the newest one in minimal form and returns its historyId if available. If the message disappeared or no usable history ID is found, it returns the fallback floor.

**Call relations**: GmailConnector._backfill calls this after it has listed all message IDs. Its result is handed back to GmailConnector.paginate as the next cursor, allowing later runs to use Gmail’s history endpoint instead of repeating the same backfill forever.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 203–238)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This asks Gmail what changed since a saved history ID. It finds messages that were added and messages that were deleted, and notices when the saved cursor is too old to use.

**Data flow**: It receives an HTTP client and a saved history ID. It walks through Gmail history pages, extracts message IDs from added and deleted entries, keeps the latest returned history ID, removes IDs that were both added and deleted, and returns added IDs, deleted IDs, and the next cursor. If Gmail returns 404, it raises CursorExpired so the system can resync from scratch.

**Call relations**: GmailConnector.paginate calls this during normal incremental syncs. This function uses _message_ids to pull IDs out of Gmail’s nested history records, then hands the added and deleted sets back to paginate for body fetching and tombstone reporting.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 240–254)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns Gmail message IDs into full flattened records. It skips messages that vanish between the change listing and the body fetch, which can happen in a live mailbox.

**Data flow**: It receives an HTTP client and a list of message IDs. For each ID, it asks Gmail for the full message; if Gmail says that one message no longer exists, it moves on. Each fetched raw message is passed through _flatten_message, and the function returns the list of flattened records.

**Call relations**: GmailConnector.paginate calls this after _backfill or _history has produced message IDs. It hands raw Gmail payloads to _flatten_message so paginate can yield records that the rest of the system can store and render.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 256–276)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a flattened Gmail record into a readable email page. It builds a title and a human-friendly text block with From, To, Cc, Subject, and the message body.

**Data flow**: It receives one record and its stream description. For the messages stream, it reads the subject, sender, recipients, and body fields, formats them into email-like text, and returns a title plus rendered content. For other streams, it falls back to the parent connector’s default rendering.

**Call relations**: The broader source system calls render when it needs recallable text from a synced record. This method relies on _str, _format_contact, _format_recipients, and _message_body to turn stored fields into clean prose.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 279–288)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This pulls message IDs out of Gmail history entries. Gmail wraps each ID inside a small nested object, so this helper extracts only valid string IDs.

**Data flow**: It receives any value that should be a list of history entries. It ignores malformed entries, looks inside each entry’s message object, collects non-empty string IDs, and returns those IDs as a list.

**Call relations**: GmailConnector._history uses this while reading Gmail’s added and deleted history sections. It keeps the history-walking code focused on sync flow instead of low-level shape checking.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 291–318)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts Gmail’s raw full-message response into the simple record shape the system stores. It lifts out the useful email fields and decodes the body parts.

**Data flow**: It receives a raw Gmail message dictionary. It reads selected headers, extracts plain-text and HTML bodies, parses sender and recipient addresses, collects labels, and returns one flat dictionary with fields such as id, subject, from_handle, to, cc, body_text, body_html, and direction.

**Call relations**: GmailConnector._fetch_bodies calls this for every fetched message. It calls _extract_bodies for MIME body text, _parse_first_address for the sender, and _addresses for recipient lists before handing the clean record back to the sync page builder.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 321–335)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail MIME payload for the first plain-text body and the first HTML body. MIME is the email format that can nest many parts, such as text, HTML, and attachments.

**Data flow**: It receives the message payload dictionary. It walks through the payload tree, decodes the first text/plain and text/html parts it finds, and returns a pair: plain text or none, and HTML text or none.

**Call relations**: _flatten_message calls this while building a flat stored record. Inside it, the nested walk helper does the actual tree traversal and uses _b64url_decode when it finds encoded body data.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 325–332)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This inner helper walks one branch of the email’s nested MIME payload tree. It is like opening folders inside folders until it finds the readable text parts.

**Data flow**: It receives one MIME part dictionary. If that part is a plain-text or HTML body with encoded data, and that kind has not already been found, it decodes and stores it. Then it repeats the same process for each child part.

**Call relations**: _extract_bodies starts the walk at the top payload. Whenever this helper finds encoded body text, it hands the string to _b64url_decode so the stored result becomes readable characters.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 338–344)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes Gmail’s body text encoding into normal text. Gmail uses URL-safe base64 and may omit padding characters, so this helper repairs the padding before decoding.

**Data flow**: It receives an encoded string. It adds the needed equals-sign padding, decodes the URL-safe base64 bytes, converts them to UTF-8 text while replacing invalid characters, and returns the decoded text. If the input cannot be decoded as base64, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this when it finds a text/plain or text/html body. The decoded text then becomes part of the flat message record produced later by _flatten_message.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 347–354)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This reads the first email address from a header such as From. It separates the mailbox address from the display name, for example turning “Jane Doe <jane@example.com>” into address and name parts.

**Data flow**: It receives a header string or nothing. If the header is missing or contains no parsed addresses, it returns two empty values. Otherwise it lowercases the first email address, keeps the display name if present, and returns both.

**Call relations**: _flatten_message calls this for the From header. It uses Python’s email address parser so the rest of the connector can store sender fields in a predictable shape.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 357–364)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This reads all email addresses from a recipient header such as To or Cc. It turns a possibly messy header string into a clean list of address records.

**Data flow**: It receives a header string or nothing. If missing, it returns an empty list. Otherwise it parses all addresses, lowercases each address, preserves each display name when present, and returns a list of dictionaries with handle and display_name.

**Call relations**: _flatten_message calls this for To and Cc headers. The resulting recipient lists are later formatted by GmailConnector.render through _format_recipients.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 367–372)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one stored contact into a display string. It prefers the friendly form “Name <address>” when a name is available, otherwise it uses just the address.

**Data flow**: It receives a possible email address and display name. If the address is not a non-empty string, it returns an empty string. Otherwise it combines the display name and address when possible, or returns only the address.

**Call relations**: GmailConnector.render uses this for the sender line, and _format_recipients uses it for each recipient. It is the small formatting step that makes rendered mail look familiar.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 375–382)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient records into the comma-separated text used in email headers. It turns stored recipient data back into a normal To or Cc line.

**Data flow**: It receives any value that should be a list. If it is not a list, it returns an empty string. For each dictionary item, it calls _format_contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this while building the To and Cc lines. It delegates each individual contact to _format_contact so sender and recipient formatting stay consistent.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 385–393)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best readable body text for a rendered email. It prefers plain text, falls back to cleaned HTML, and finally uses Gmail’s short snippet if no full body is available.

**Data flow**: It receives a flattened message record. It checks body_text first and returns the trimmed text if present. If not, it checks body_html and uses _HtmlText.extract to strip HTML tags. If neither body is usable, it returns the trimmed snippet or an empty string.

**Call relations**: GmailConnector.render calls this after building the header lines. When HTML must be cleaned, it hands the raw HTML to _HtmlText.extract, which turns it into readable plain text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 396–397)

```
def _str(value: Any) -> str
```

**Purpose**: This safely treats a value as a string only if it really is one. It prevents non-string data from accidentally appearing in rendered email text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GmailConnector.render uses this when reading the subject. That keeps title-building simple and avoids rendering unexpected data types.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 404–406)

```
def __init__(self) -> None
```

**Purpose**: This prepares a small HTML-to-text parser for one email body. It creates an empty list where pieces of readable text will be collected.

**Data flow**: It receives no outside data beyond the new parser object. It initializes the standard HTML parser with automatic character reference conversion, then sets up an empty parts list. The parser object is ready to receive HTML.

**Call relations**: _HtmlText.extract creates an instance through this initializer before feeding it raw HTML. The later parser callbacks add text and line breaks into the parts list.


##### `_HtmlText.extract`  (lines 409–414)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This converts an HTML email body into plain readable text. It keeps the words, drops tags and attributes, and inserts line breaks around block-like elements.

**Data flow**: It receives raw HTML text. It creates a parser, feeds the HTML through it, joins the collected pieces, collapses extra spaces on each line, removes empty lines, and returns clean plain text.

**Call relations**: _message_body calls this when an email has HTML but no usable plain-text body. During parsing, the HTML parser calls handle_data, handle_starttag, and handle_endtag to collect text and spacing.


##### `_HtmlText.handle_data`  (lines 416–417)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records visible text found inside the HTML. It is called by the parser whenever it reaches character data between tags.

**Data flow**: It receives a text fragment from the HTML parser. It appends that fragment to the parser’s internal parts list and returns nothing.

**Call relations**: _HtmlText.extract indirectly triggers this by feeding HTML into the parser. The collected fragments are later joined and cleaned into the final plain-text body.


##### `_HtmlText.handle_starttag`  (lines 419–421)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when an opening HTML tag represents a block boundary, such as a paragraph or table row. That keeps the extracted text from running together.

**Data flow**: It receives a tag name and its attributes. If the tag is one of the known block-style tags, it appends a newline to the parser’s parts list. It ignores the attributes and returns nothing.

**Call relations**: _HtmlText.extract causes this to run while parsing raw HTML. Its newlines work together with handle_endtag and handle_data so the final cleaned text has readable paragraph breaks.


##### `_HtmlText.handle_endtag`  (lines 423–425)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a closing HTML tag marks the end of a block. It helps preserve the rough structure of the email after tags are removed.

**Data flow**: It receives a tag name. If the tag is in the known block tag set, it appends a newline to the parser’s parts list. It returns nothing.

**Call relations**: _HtmlText.extract triggers this during HTML parsing. Along with handle_starttag, it supplies the spacing that makes the stripped HTML body readable instead of one long line.


### `extensions/sources/ufo_ext_sources/providers/googlecalendar.py`

`io_transport` · `source sync runs`

This connector is the bridge between Google Calendar and the rest of the source-sync system. Without it, calendar events would stay inside Google and could not be indexed, searched, or recalled by this project.

It reads from Google Calendar's events API, which is the web endpoint that lists calendar events. The first time it runs, it looks back 90 days and asks Google for events, including deleted ones. Google then gives back a special sync token, which works like a bookmark. On later runs, the connector sends that bookmark back to Google and receives only changes since the last run.

The file produces two views of the same calendar data. The main `calendar_events` stream keeps one record per event, including title, time, location, organizer, description, and attendee handles. The `event_attendees` stream turns each event into one row per invited person, like splitting a meeting invite into a guest list.

It also deals with important failure cases. If Google's bookmark has expired, it tells the core sync system to start fresh. If the user did not grant Calendar permission, it marks the stream as skipped rather than failed. When records are rendered for recall, calendar events become readable text with the title, time, location, attendees, and description.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 51–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads pages of events from Google Calendar and turns them into sync pages for the rest of the system. It supports both the event stream and the attendee stream, and it uses Google's sync token as a bookmark so later runs only fetch changes.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. If there is a cursor, it asks Google for changes since that cursor; if not, it asks for events from the last 90 days. For each Google event it sees, it either turns the event into a normal event record, turns its invitees into attendee records, or records a deletion when the event was cancelled. It yields `StreamPage` objects containing records, deleted IDs, and finally the next cursor to save.

**Call relations**: This is the main reading loop called by the source-sync runner through the connector interface. It hands raw event objects to `_flatten_event` for the calendar event stream and to `_flatten_attendees` for the attendee stream. If Google says the sync token is no longer valid, it raises `CursorExpired` so the wider sync system can refetch from scratch; if Calendar access is missing, it raises `StreamSkipped` so the run records a clean skip.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 5 external calls (__init__, __init__, now, timedelta, refused_for_scope).


##### `GoogleCalendarConnector.render`  (lines 111–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced calendar event record into human-readable text for recall or indexing. For non-event streams, it falls back to the generic rendering behavior from the parent connector.

**Data flow**: It receives a record and the stream it came from. For calendar events, it pulls out the title, time range, location, attendee handles, and description, then builds a simple text block. It returns two pieces: a short title and the full readable body.

**Call relations**: The broader system calls this when it needs text to store or search from a synced record. It uses `_str` to safely treat missing or non-text titles as an empty string, then formats the rest of the event itself.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 139–166)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts one raw Google Calendar event into the flat event record shape used by the sync system. This makes Google's nested event data easier for the rest of the project to store and search.

**Data flow**: It receives one Google event dictionary. It reads fields such as the event ID, created and updated times, summary, description, location, start and end times, organizer, status, calendar UID, recurrence ID, and attendee list. It normalizes times through `_parse_when`, cleans attendee entries through `_attendee`, lowercases email handles where appropriate, and returns one plain event record.

**Call relations**: It is used by `GoogleCalendarConnector.paginate` when the requested stream is `calendar_events` and the event is not cancelled. It delegates the smaller jobs of attendee cleanup and time cleanup to `_attendee` and `_parse_when` so the main event conversion stays readable.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 169–175)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Cleans up a single attendee entry for inclusion inside a calendar event record. It keeps just the invitee's handle, display name, and response in the project's preferred wording.

**Data flow**: It receives one attendee dictionary from Google. It lowercases the attendee email address, copies the display name if present, translates Google's response status such as `needsAction` into the system's form such as `needs_action`, and returns a small attendee dictionary.

**Call relations**: It is called while `_flatten_event` builds the attendee list that is folded into an event record. It does not fetch anything itself; it only reshapes one already-loaded attendee.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 178–204)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Turns one Google Calendar event into separate attendee records, one per invited person. This lets the system treat event attendance like a relationship table: this person is connected to this event with this role and response.

**Data flow**: It receives one raw Google event dictionary. It reads the event ID, timestamps, organizer email, and attendee list. For each attendee with an email address, it builds a stable row ID from the event ID and email, lowercases the handle, records the display name, response, role, and whether the attendee is the current user, then returns the list of rows.

**Call relations**: It is used by `GoogleCalendarConnector.paginate` when the requested stream is `event_attendees` and the event is not cancelled. For each attendee row, it asks `_attendee_role` to decide whether the person is the organizer, a room or resource, optional, or required.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 207–214)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: Decides what role an invitee has in an event. It turns Google's attendee flags into one simple label the rest of the system can understand.

**Data flow**: It receives one attendee dictionary and a separate truth value saying whether that attendee matches the organizer. It checks, in order, whether the attendee is the organizer, a resource such as a room, optional, or otherwise required. It returns one role string.

**Call relations**: It is called from `_flatten_attendees` while building the per-attendee stream. This keeps the role decision in one small place instead of spreading the same checks through the attendee conversion code.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 217–226)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: Normalizes Google Calendar's two different time formats into one timestamp-like string. Google uses one shape for timed events and another for all-day events, so this function smooths over that difference.

**Data flow**: It receives a value that may be a Google time dictionary. If it contains `dateTime`, it returns that value as text. If it contains an all-day `date`, it turns that date into a midnight UTC timestamp string. If the input is missing or not in a recognized form, it returns nothing.

**Call relations**: It is called by `_flatten_event` for both the event start and end fields. This means the rest of the event record can use `starts_at` and `ends_at` without caring which Google time format was originally used.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 229–230)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already text. It prevents the renderer from accidentally treating missing or non-text values as titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: It is used by `GoogleCalendarConnector.render` when building the display title for a calendar event. This small guard lets rendering continue cleanly even when Google data is incomplete or oddly shaped.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledocs.py`

`io_transport` · `source sync and document rendering`

This connector is the bridge between Google Docs and the rest of the source-sync system. Its job is to ask Google Drive, “Which Google Docs can this user or grant see?”, then fetch each document from the Google Docs API and turn it into readable prose. Without this file, Google Docs would not appear as recallable text in the system.

The flow has two steps. First, it lists files through Google Drive, filtered to real Google Docs, not trashed files. It also uses a saved timestamp cursor, called a watermark, so later syncs only ask for documents changed since the last run. This is like checking only the mail that arrived after yesterday instead of reopening every letter.

Second, for each listed file, it asks the Docs API for the full document. If a file can be listed but not opened, or has disappeared, the connector keeps going and creates a minimal “stub” record instead of failing the whole sync. If Google refuses the overall listing because the grant lacks permission, the stream is skipped with a clear reason.

Finally, the file includes rendering logic. Google Docs store text in a nested document tree, not as one simple string. The renderer walks paragraph text runs in order and produces plain text with a simple heading.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 53–89)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches changed Google Docs in batches that the rest of the sync system can process. It combines Drive file metadata with the full Docs document, and yields records page by page so large accounts do not need to be loaded all at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It asks `_iter_doc_files` for Drive file pages changed after that cursor, then asks `_document` for each file’s full document. It builds one combined record per document, including title, URL, created and updated times, MIME type, and the original Drive file details, then outputs lists of up to 100 records at a time. If Google refuses access because the grant lacks the needed permission, it turns that into a skipped stream instead of an unexplained crash.

**Call relations**: This is the main paging method used by the source-sync framework for the Google Docs stream. During a sync, it calls `_iter_doc_files` to discover candidate documents, calls `_document` to fetch each one, and uses `google.refused_for_scope` to decide whether an access error should become a clean `StreamSkipped` result.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files); 1 external calls (refused_for_scope).


##### `GoogleDocsConnector._document`  (lines 91–100)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: Fetches one Google Doc by its file ID from the Google Docs API. It protects the sync from failing just because one document cannot be opened.

**Data flow**: It receives an HTTP client and a Google file ID. It requests the full document from the Docs API and returns the document data. If Google says the document is forbidden or not found, it returns a small record containing only the document ID, so the caller can still make a placeholder entry. Other errors are passed upward.

**Call relations**: `paginate` calls this after Drive has listed a document file. Its result is merged with Drive metadata to form the final synced record.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 102–129)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Lists Google Doc files visible through Google Drive, one Drive results page at a time. It applies the saved cursor so the connector can do incremental syncs instead of re-reading everything.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for untrashed Google Docs, adds a “modified after this time” filter when a cursor exists, and repeatedly requests Drive pages using Google’s next-page token. Each response’s `files` value is normalized into a list and yielded; when there is no next-page token, iteration stops.

**Call relations**: `paginate` relies on this function to discover which documents should be fetched. This function uses `list_or_empty` so an absent or oddly shaped `files` field becomes a safe empty list rather than breaking the loop.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 131–136)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a synced Google Docs record into the plain text form the system can store, index, or show later. It adds a simple heading that identifies the source and stream.

**Data flow**: It receives a document record and the stream description. It reads the title if present, asks `_plain_text` to extract the body text, builds a heading like `# googledocs documents: Title`, and returns both the title and the finished text block.

**Call relations**: This is called when a fetched record needs to become readable content. It delegates the tricky Google Docs document-tree parsing to `_plain_text`, then wraps the result in a consistent source heading.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 139–155)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: Extracts the visible paragraph text from the nested structure used by the Google Docs API. It ignores document parts that do not contain paragraph text runs.

**Data flow**: It receives a document record. It looks inside `body.content`, walks each paragraph, then walks each paragraph element looking for text runs, which are the small pieces of actual text stored by Google. It joins those pieces in order, trims extra whitespace at the ends, and returns one plain string.

**Call relations**: `render` calls this when preparing a document for recall or indexing. It is the small helper that turns Google’s structured document data into normal readable prose.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googledrive.py`

`io_transport` · `source sync runs`

This connector is the Google Drive “reader” for the project. It solves the problem of keeping a local searchable copy of Drive metadata without owning the user’s Google token itself. The runner provides an authenticated HTTP client, and this file uses it to ask Google’s API for Drive records.

The main stream is `files`. On a first sync, it lists all non-trashed files, ordered by modification time. At the end of that first pass it asks Google for a changes token, which works like a bookmark: “next time, start from here.” On later syncs it uses that bookmark to read only Drive changes. If a file was removed or trashed, the connector emits a delete marker instead of a normal record. If Google says the bookmark is too old, it raises `CursorExpired` so the broader system knows to start fresh.

Other streams work differently. Shared drives are fully re-read each time. Permissions, comments, and revisions are fetched by first listing every file, then asking Google for that file’s child collection. Some files may refuse those child requests, so the connector quietly skips common per-file refusals. If the whole grant lacks Drive access, the stream is recorded as skipped rather than failed.

Finally, `render` turns a Drive file record into readable text with its name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 89–121)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main dispatcher for reading a Google Drive stream. It looks at which stream the system asked for, then sends the work to the right paging method for files, shared drives, permissions, comments, or revisions.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor, which is the saved bookmark from a previous run. It chooses the correct Google Drive reading path, yields batches of records or special stream pages, and may produce a new cursor so the next run can continue from the right place. If Google refuses because the grant lacks Drive scope, it turns that into a stream skip instead of a hard failure.

**Call relations**: The sync engine calls this when it wants records from a Google Drive stream. For file streams it either calls `_paginate_file_changes` for an existing cursor or `_paginate_files` followed by `_start_page_token` for a first run. For shared drives it calls `_paginate_shared_drives`, and for child data like permissions, comments, and revisions it calls `_paginate_file_children`. It also uses Google refusal checking to decide whether to raise `StreamSkipped`.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 2 external calls (__init__, refused_for_scope).


##### `GoogleDriveConnector._paginate_files`  (lines 123–148)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads pages of Google Drive files from the live file listing endpoint. It is used for the first file sync and also as the starting list when fetching each file’s permissions, comments, or revisions.

**Data flow**: It receives an HTTP client and an optional modification-time cursor. It builds a Google Drive query for non-trashed files, optionally limited to files modified after the cursor, then repeatedly asks Google for one page at a time. Each response’s `files` list is cleaned into a normal list, yielded if non-empty, and the next page token is followed until there are no more pages.

**Call relations**: `paginate` calls this during the initial `files` stream read. `_paginate_file_children` also calls it so it can visit every file before fetching that file’s child records. It relies on `list_or_empty` to safely treat missing or malformed lists as empty lists.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 150–155)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current changes bookmark. The connector saves this token after a full first scan so future runs can read only what changed.

**Data flow**: It receives an HTTP client, calls Google’s start-page-token endpoint, reads the `startPageToken` value from the response, and returns it only if it is a real non-empty string. Otherwise it returns nothing.

**Call relations**: `paginate` calls this after it has finished the first full file listing. The returned token is wrapped in a `StreamPage` so the broader sync system can store it as the next cursor.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 157–202)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads Google Drive’s change feed using a saved cursor. It lets later syncs update changed files and delete records for files that were removed or trashed, without scanning the whole Drive again.

**Data flow**: It receives an HTTP client and a cursor token from a previous run. It asks Google for change pages, turns changed file objects into records, turns removed or trashed files into delete IDs, and yields `StreamPage` objects containing both. When Google gives another page token, that becomes the next cursor; when the feed ends, Google’s fresh start token becomes the cursor for the next run. If Google says the old token expired with status 410, it raises `CursorExpired`.

**Call relations**: `paginate` calls this whenever the `files` stream already has a cursor. It creates `StreamPage` results so the sync layer can apply both upserts and deletions. It uses `list_or_empty` to safely walk the response’s change list and `CursorExpired` to tell the caller that a full refresh is needed.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 204–221)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the list of shared drives visible to the grant. Unlike file changes, it simply re-lists all shared drives each time.

**Data flow**: It receives an HTTP client, asks Google for shared drive pages, extracts the `drives` list from each response, yields any records it finds, and follows `nextPageToken` until Google has no more pages.

**Call relations**: `paginate` calls this for the `shared_drives` stream. It uses `list_or_empty` to turn the response’s drive list into a safe iterable before yielding records to the sync system.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 223–261)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads per-file child data: permissions, comments, or revisions. It first finds files, then asks Google for the requested child collection under each file.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. It lists all files with `_paginate_files`, takes each file ID, then requests that file’s permissions, comments, or revisions page by page. If a child record stream has a cursor field, it filters out older records. Records that remain are yielded with extra context: the parent `file_id` and `file_name`. If Google says a specific file’s child collection is forbidden or missing, it skips that file’s child data and continues.

**Call relations**: `paginate` calls this for the `permissions`, `comments`, and `revisions` streams. This function depends on `_paginate_files` for the parent file list and `list_or_empty` for safe response parsing. It is the fan-out step: one file list becomes many per-file API calls.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 263–278)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a short human-readable text body. That makes stored file metadata easier to display or search later.

**Data flow**: It receives one record and the stream it came from. For non-file streams, it delegates to the base connector’s normal rendering. For the `files` stream, it reads the file name, MIME type, owners, and web link, then returns a title and a small text block. Missing or non-string names are safely converted to an empty string through `_str`.

**Call relations**: The broader source system calls this when it needs a readable representation of a synced record. For Google Drive files it builds a custom summary itself, and for all other streams it hands the work back to the parent `RestConnector` behavior. It calls `_str` to avoid treating non-text values as titles.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 281–282)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only if it is already a string. It prevents accidental non-text values from being used where text is expected.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: `GoogleDriveConnector.render` calls this when preparing the file title. It keeps rendering simple and predictable when Google records are missing a name or contain an unexpected value.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/googlemeet.py`

`io_transport` · `source sync`

This connector talks to the Google Meet REST API, which is Google’s web interface for programs to request Meet data. It treats each conference record as one meeting-shaped page. For each meeting, it looks for generated transcripts and smart notes, then gathers enough detail to make the meeting useful later: times, links to Google Docs, transcript lines, and any available plain text from AI notes.

The file is careful about time. During an incremental sync, it starts from the last saved meeting start time, but looks back one day. This matters because Google may create transcripts or notes after a meeting ends. The connector can revisit recent meetings without endlessly scanning old history.

The flow is like a librarian checking a list of recent meetings, opening each folder, copying any transcript pages and summary notes, and filing only folders that contain something useful. If Google says the account is not allowed to read Meet artifacts, the stream is marked as skipped rather than crashing the whole run. If a linked Google Doc for notes is missing or forbidden, the connector keeps the document link and continues instead of failing.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 57–91)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main fetch loop for the Google Meet stream. It lists conference records from Google, fetches their transcript and smart-note details, and yields them in pages for the rest of the system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It builds a Google Meet request, adds a one-day lookback filter when a cursor exists, follows Google’s page tokens, and turns each useful conference into a record. It outputs StreamPage objects containing meeting records and the next cursor, and it may raise StreamSkipped if Google refuses access because the account lacks the needed permission.

**Call relations**: The sync driver calls this when it wants Google Meet data. Inside the loop, it uses _lookback to widen the time window, _max_start_time to advance the saved cursor, and _conference_record to fill in each meeting. If Google’s error looks like a permission refusal, it asks google.refused_for_scope and then reports the stream as skipped.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 3 external calls (__init__, list_or_empty, refused_for_scope).


##### `GoogleMeetConnector._conference_record`  (lines 93–116)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the complete internal record for one Google Meet conference. It gathers the meeting’s transcripts and smart notes and packages them with basic meeting metadata.

**Data flow**: It receives one conference object from Google. It reads the conference resource name, asks _artifacts for transcript sessions and smart-note sessions, expands those through _transcript and _smart_note, and creates a dictionary with IDs, title, times, space, transcripts, and notes. The result is one meeting record ready to be emitted or rendered.

**Call relations**: paginate calls this for each conference returned by Google. This function is the meeting assembler: it delegates list retrieval to _artifacts, transcript shaping to _transcript, note shaping to _smart_note, and ID cleanup to _resource_id and _str.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 118–135)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches a paged list of artifacts under a conference, such as transcripts or smart notes. It hides Google’s repeated page-token requests from the rest of the connector.

**Data flow**: It receives an HTTP client, a parent conference name, and the artifact collection name to fetch. If there is no parent name, it returns an empty list. Otherwise it calls the Meet API page by page, collects the artifact objects from each response, and returns one combined list.

**Call relations**: _conference_record calls this twice for each meeting: once for transcripts and once for smart notes. It supplies the raw artifact lists that are then expanded by _transcript or _smart_note.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 137–149)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google into a cleaner record for the system. It includes transcript metadata, the linked Google Docs destination, and the per-speaker transcript entries.

**Data flow**: It receives an HTTP client and a transcript object from Google. It extracts the transcript name, ID, state, start and end times, reads any Google Docs destination, and calls _transcript_entries to fetch the actual spoken lines. It returns a dictionary describing the transcript and its entries.

**Call relations**: _conference_record calls this for each transcript artifact. It relies on _docs_destination to preserve document links, _transcript_entries to fetch dialogue, and helper functions to normalize strings and resource IDs.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 151–184)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual lines inside a transcript, including speaker information and timing. These entries become the readable meeting dialogue in the rendered page.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is blank, it returns an empty list. Otherwise it follows Google’s entry pages, converts each entry into a simpler dictionary with ID, participant, text, language, and times, and returns the collected list. If Google says the transcript document is missing or forbidden, it returns whatever entries it already gathered instead of failing.

**Call relations**: _transcript calls this while expanding a transcript session. Later, render uses the entries indirectly through _transcripts_section and _dialogue to produce readable speaker-by-speaker text.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 186–201)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note session into a system record. When possible, it also reads the linked Google Doc so the AI summary text is stored directly, not just as a link.

**Data flow**: It receives an HTTP client and a smart-note object. It extracts the note ID, name, state, times, and Google Docs destination. If there is a document ID, it asks _document_text for the plain text; when text is returned, it adds it as the note body. It outputs a note dictionary that can be rendered later.

**Call relations**: _conference_record calls this for each smart-note artifact. It uses _docs_destination for the Google Docs link and _document_text to inline the summary when the same account can read it.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 203–212)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads the plain text from a Google Docs document linked by a smart note. It lets the final meeting page include the AI summary itself instead of only pointing to the document.

**Data flow**: It receives an HTTP client and a Google Docs document ID. It safely inserts the document ID into the Docs API URL, fetches the document, and passes the response to _plain_text. If the document is missing or access is denied, it returns an empty string; other HTTP errors are allowed to bubble up.

**Call relations**: _smart_note calls this only when a smart note has a linked Docs document. This function bridges from Meet metadata to the Docs API and then hands the raw document structure to _plain_text for extraction.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 214–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a stored Google Meet record into a human-readable page title and body. It is the final step that turns API data into prose someone can search and read.

**Data flow**: It receives one meeting record and the stream description. For the meeting_artifacts stream, it builds a title, a labeled block of meeting metadata, a transcript section, and an AI-summary section, then returns the title and combined text. For other streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The source framework calls this after records have been fetched. It uses _labeled for compact metadata, _transcripts_section for transcript content, _smart_notes_section for AI summaries, and _str to avoid accidental non-text values.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 234–250)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcripts for a meeting into a readable Markdown-style section. It includes transcript status and document links, followed by dialogue when entries are available.

**Data flow**: It receives an unknown value that should contain transcript records. It turns missing or non-list data into an empty list, then for each transcript builds a small metadata block and formats entries through _dialogue. It returns one text section, or an empty string if there are no transcripts.

**Call relations**: render calls this while building the final meeting page. It delegates label formatting to _labeled and spoken-line formatting to _dialogue so that the transcript section stays readable.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 253–269)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats Google Meet smart notes into a readable AI summaries section. It shows note status, timing, document link, and any summary body that was successfully read from Google Docs.

**Data flow**: It receives an unknown value that should contain smart-note records. It normalizes that into a list, extracts each note’s metadata and body text, and joins them into one Markdown-style section. If there are no notes, it returns an empty string.

**Call relations**: render calls this when producing the final meeting page. It uses _labeled for the metadata block and _str to safely treat possibly missing values as text.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 272–285)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into a simple speaker-by-speaker conversation. It also joins consecutive lines from the same speaker so the transcript reads less choppy.

**Data flow**: It receives an unknown value that should be a list of transcript entries. It skips empty text, finds a display name for each participant, appends new speaker lines, and merges text into the previous line when the same speaker continues. It returns a newline-separated dialogue string.

**Call relations**: _transcripts_section calls this for each transcript. It uses _speaker to choose a friendly speaker label and _str to safely read entry text.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 288–301)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. Google Docs sends documents as many small content objects; this function flattens those text pieces into normal text.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the body content, finds paragraph text runs, collects their text chunks, joins them together, trims extra whitespace, and returns the result. It does not change the input record.

**Call relations**: _document_text calls this after successfully fetching a Google Doc. This is the document unpacker that turns Google’s structured response into text the meeting page can include.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 304–311)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls Google Docs destination information out of a Meet transcript or smart-note object. It preserves both the document ID and the export URL when Google provides them.

**Data flow**: It receives an artifact record. If the record has a docsDestination object, it reads the document and exportUri fields and returns them as docs_document and docs_url. If that object is absent or not shaped as expected, it returns an empty dictionary.

**Call relations**: _transcript and _smart_note both call this while building their records. It gives later steps either a link to show in the rendered page or a document ID that _smart_note can use to fetch summary text.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 314–320)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest meeting start time from a page of conferences and the existing cursor. It helps the sync remember how far it has scanned.

**Data flow**: It receives a list of conference objects and the current cursor string, if any. It compares each conference startTime with the current value and keeps the latest string timestamp. It returns the updated cursor, or the original value if nothing newer is found.

**Call relations**: paginate calls this after each conference list response. The returned value becomes the next cursor in the StreamPage, so the next sync can resume from the right point.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 323–325)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This shifts a saved cursor back by one day. It gives Google time to finish creating transcripts and smart notes after a meeting has already ended.

**Data flow**: It receives an ISO timestamp string, parses it as a date and time, subtracts the configured one-day lookback, and returns a timestamp string in the format Google expects. It does not store anything itself.

**Call relations**: paginate calls this when it has a cursor and needs to build a Meet API filter. The widened filter lets the connector refetch recent meetings that may have gained new artifacts.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 328–329)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the last part of a Google resource name. Google names often look like paths; this helper keeps just the final useful identifier.

**Data flow**: It receives a resource name string. If the string is not empty, it splits at the final slash and returns the last piece; otherwise it returns an empty string. It does not modify anything.

**Call relations**: Several record-building functions call this when creating stable IDs for conferences, transcripts, notes, and transcript entries. _speaker also uses it to turn a participant resource name into a readable fallback label.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 332–334)

```
def _speaker(value: Any) -> str
```

**Purpose**: This chooses a display label for a transcript speaker. If Google provides a participant resource name, it uses the final ID; otherwise it falls back to “Participant.”

**Data flow**: It receives a participant value that may or may not be text. It first turns it into a safe string, then extracts the final resource ID. It returns that ID, or the word “Participant” when no usable name exists.

**Call relations**: _dialogue calls this while formatting transcript entries. It provides the speaker name that appears before each line of dialogue.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 337–338)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only when it is actually text. It prevents numbers, dictionaries, missing values, or other shapes from accidentally appearing in rendered output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It has no side effects.

**Call relations**: This helper is used throughout fetching and rendering whenever the connector reads optional fields from Google responses. It keeps functions such as render, _dialogue, _docs_destination, and the record builders simple and safe.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 341–342)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats small metadata blocks such as start time, end time, state, and document URL. It leaves out empty values so the final page does not contain blank labels.

**Data flow**: It receives a list of label-and-value pairs. For each pair with a non-empty value, it creates a line like “label: value,” then joins those lines with newlines. It returns the finished text block.

**Call relations**: render, _transcripts_section, and _smart_notes_section call this when building the readable page. It gives all those sections the same compact style for metadata.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/providers/googlesheets.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and a user's Google Sheets. It first asks Google Drive for spreadsheet files, because Drive knows which spreadsheets exist and when each file was last changed. Then, for each spreadsheet, it asks the Sheets API for the spreadsheet details, its tabs, and, when needed, the cell values in each tab.

The file produces three related streams: one record per spreadsheet, one record per tab, and one record per tab's grid of values. Think of it like unpacking a workbook: first the book, then its pages, then the writing on each page.

A major part of this file is safe incremental syncing. It stores a cursor, which is a bookmark saying “we have read up to this modified time.” If Google refuses access to one file, the connector does not stop the whole sync or hold back every later file. Instead, it records that file id in the cursor and retries it later. If the refusal is about the whole Google grant, such as missing Drive or Sheets permission, the stream is skipped. If it is a quota or unexpected error, the run fails rather than guessing.

The connector is read-only. Its render method turns raw spreadsheet data into simple text, so synced Sheets can be recalled or searched later.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 155–205)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for a Google Sheets stream. It walks through changed spreadsheets, turns each one into records for the requested stream, and emits pages of records with a cursor bookmark.

**Data flow**: It receives an HTTP client, a stream choice, and an optional cursor from a previous run. It decodes the cursor into a saved modified-time watermark plus any file ids that need retrying, reads matching Drive files, asks helper functions to build records, and yields pages. As it goes, it updates the cursor with the newest listed file time and any files that are still refused.

**Call relations**: The runtime calls this when it wants records from the connector. It relies on _spreadsheet_visits for normal Drive listing, _visit_records to turn each visited spreadsheet into stream records, _carried_visit to retry previously refused files, and _encode_cursor/_decode_cursor/_settled to keep the progress bookmark honest.

*Call graph*: calls 7 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _settled); 2 external calls (__init__, refused_for_scope).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 207–232)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads pages of spreadsheet file metadata from Google Drive. It is the part that asks, “Which Google Sheets files are visible, untrashed, and changed since our last bookmark?”

**Data flow**: It receives an HTTP client and an optional watermark time. It builds a Drive query for spreadsheet files, adds the watermark filter when present, follows Drive page tokens, cleans the returned file list into a list, and yields each non-empty batch of file dictionaries.

**Call relations**: _spreadsheet_visits calls this as the first step of a normal sync. It does not fetch Sheets-specific details itself; it only supplies Drive file records for the next helper to enrich.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 234–242)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This turns Drive file batches into individual spreadsheet visits. A visit is the connector's internal note about one file: its id, its assembled spreadsheet record if available, and whether access was refused.

**Data flow**: It receives an HTTP client and watermark, reads batches from _iter_spreadsheet_files, skips malformed entries without a usable id, and calls _file_visit for each valid spreadsheet id. It yields one _FileVisit at a time.

**Call relations**: paginate uses this during the main Drive listing phase. This helper sits between the broad Drive list and the per-file Sheets lookup done by _file_visit.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 244–258)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This retries a spreadsheet that was refused in an earlier sync and saved in the cursor. It checks whether the file still exists, is trashed, is still refused, or can now be read.

**Data flow**: It receives an HTTP client and a file id from the carried-refusal list. It asks Drive for that one file's metadata, including whether it is trashed. If the file is gone or still individually refused, it returns a visit with no full record; if it is trashed, it marks it settled; otherwise it calls _file_visit to build the normal spreadsheet record.

**Call relations**: paginate calls this after the normal listing has been drained, but only for carried ids that were not already encountered in the listing. It uses _is_per_file_refusal to decide whether an error is safe to treat as one-file trouble instead of a whole-stream failure.

*Call graph*: calls 2 internal fn (_file_visit, _is_per_file_refusal); called by 1 (paginate); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._file_visit`  (lines 260–287)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This builds the connector's main record for one spreadsheet. It combines Drive metadata, such as timestamps and web link, with Sheets metadata, such as title and tab list.

**Data flow**: It receives an HTTP client, a spreadsheet id, and the Drive file dictionary. It asks the Sheets API for spreadsheet details. If that request is refused for just this file, it falls back to a minimal record using Drive's name and marks the visit as refused. It returns a _FileVisit containing the merged record and refusal flag.

**Call relations**: _spreadsheet_visits uses this for normally listed files, and _carried_visit uses it for retried files that can still be inspected. Downstream, _visit_records decides whether that visit becomes spreadsheet, tab, or value records.

*Call graph*: calls 1 internal fn (_is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 2 external calls (__init__, error_detail).


##### `GoogleSheetsConnector._visit_records`  (lines 289–305)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This chooses how to turn one spreadsheet visit into records for the stream currently being synced. It is the small dispatcher that connects one visited file to the three stream shapes.

**Data flow**: It receives an HTTP client, a stream description, and a _FileVisit. If the visit has no record, it returns no records and preserves the refusal flag. For the spreadsheets stream it returns the spreadsheet record; for the sheets stream it calls _sheet_records; for the sheet_values stream it calls _sheet_value_records.

**Call relations**: paginate calls this for both normally listed files and carried retry files. It hands tab extraction to _sheet_records and cell-value extraction to _sheet_value_records so the main pagination loop does not need to know each stream's details.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 307–363)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This reads the cell values for each tab in a spreadsheet and turns each tab's grid into one record. It uses batched API calls so large spreadsheets with many tabs can be read efficiently.

**Data flow**: It receives an HTTP client and a spreadsheet record that already contains tab metadata. It gathers valid tab titles and ids, requests their values in chunks, checks that Google returned one answer for each requested tab, and builds value records. If a batch is refused for a file-level reason, it retries tabs one by one so only the refused tab is dropped when possible.

**Call relations**: _visit_records calls this for the sheet_values stream. It uses _quoted_sheet_range to name tabs safely in Google's A1 range syntax, _sheet_value_record to shape each output record, _is_per_file_refusal to classify errors, and raises StreamFault if Google's batch response does not match the request.

*Call graph*: calls 4 internal fn (__init__, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 3 external calls (list_or_empty, error_detail, quote).


##### `GoogleSheetsConnector.render`  (lines 365–384)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw synced records into readable text. That text is what a person or search feature can understand later, instead of only seeing API-shaped dictionaries.

**Data flow**: It receives one record and the stream it came from. For spreadsheets, it formats the spreadsheet title and tab names; for sheets, it names the parent spreadsheet; for sheet_values, it converts rows and cells into plain text. It returns a short title and a markdown-like body.

**Call relations**: The broader source system calls render after records are fetched and need to be displayed or indexed. It uses _str to safely pull text fields and _grid_text to flatten cell grids.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 387–400)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This reads the saved sync bookmark. It supports both the old simple cursor form, which is just a timestamp string, and the newer JSON form that also remembers refused file ids.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns empty starting values. If the cursor is plain text or invalid JSON, it treats it as the watermark. If it is a valid checkpoint object, it returns the watermark, refused ids, and retry marker.

**Call relations**: paginate calls this at the start of a run so it knows where to resume. It validates structured cursors with _Checkpoint, which prevents silently accepting malformed bookmark data.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 403–408)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This writes the sync bookmark that will be saved after a page. It keeps the normal modified-time watermark and, when needed, adds a short list of file ids that should be retried later.

**Data flow**: It receives the current watermark, a set of refused file ids, and an optional id showing how far carried retries have gone. If there is no watermark or no refused file ids, it returns just the watermark. Otherwise it returns a JSON checkpoint with sorted refused ids, limited to a fixed maximum.

**Call relations**: paginate calls this every time it yields records or finishes a phase. _decode_cursor reads the same format on the next run, making the two functions the save-and-load pair for progress.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 411–412)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This updates the set of refused file ids after trying one file. It answers the question, “Should this file stay on the retry list or be removed?”

**Data flow**: It receives the current refused-id set, one file id, and whether that file is still refused. If still refused, it returns a set with the id included. If not, it returns a set with the id removed.

**Call relations**: paginate calls this after _visit_records reports whether a listed or carried file is still blocked. The result is then passed to _encode_cursor so future runs retry only unresolved files.

*Call graph*: called by 1 (paginate).


##### `_is_per_file_refusal`  (lines 415–421)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This decides whether a Google error means “this one spreadsheet or tab is inaccessible” rather than “the whole connection is broken.” That distinction lets the connector skip or retry one file without throwing away the whole sync.

**Data flow**: It receives an HTTP status code and a parsed Google error detail dictionary. It returns true only for 403 or 404 responses that have Google error details, are not quota failures, and are not grant-wide permission failures.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this when Google rejects a request. It delegates grant-wide detection to _is_grant_refusal and quota detection to the Google helper module.

*Call graph*: calls 1 internal fn (_is_grant_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records); 1 external calls (is_quota_refusal).


##### `_is_grant_refusal`  (lines 424–429)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This recognizes errors that mean the user's Google authorization or project setup cannot read Drive or Sheets at all. In that case, continuing file by file would not help.

**Data flow**: It receives a parsed Google error detail dictionary. It looks for known Google reasons such as missing API configuration or insufficient permissions, and for Google service-domain details that identify credential, project, or service problems. It returns true when the error is grant-wide.

**Call relations**: _is_per_file_refusal calls this while classifying refusals. Its answer helps decide whether the connector should treat an error as one-file trouble or let the stream be skipped by the higher-level paginate error handling.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 432–453)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This creates one record for each tab inside a spreadsheet. It turns the nested Sheets API tab list into flat records that the source system can store separately.

**Data flow**: It receives a spreadsheet record with its tab metadata. It loops through valid sheet dictionaries, reads each tab's id and title, attaches the parent spreadsheet id, title, and timestamps, and returns a list of tab records.

**Call relations**: _visit_records calls this when the requested stream is sheets. The records it creates later flow through paginate as normal stream records.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 456–469)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This shapes one tab's returned cell grid into a source record. It adds parent spreadsheet and tab identity around Google's raw value range data.

**Data flow**: It receives the parent spreadsheet record, the tab title, the tab id, and one value-range response from Google. It copies the value-range fields, adds a stable id, spreadsheet information, tab information, and timestamps, and returns the finished dictionary.

**Call relations**: _sheet_value_records calls this after each successful batch or individual tab value read. The resulting records are returned to _visit_records and then paginated to the source runtime.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 472–474)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This formats a sheet tab title so Google reads it as a tab name, not as a cell reference or named range. It is important for titles with spaces, punctuation, or apostrophes.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title, then wraps the whole title in apostrophes, producing a safe A1-style sheet reference.

**Call relations**: _sheet_value_records calls this before asking Google for tab values. The quoted result is used in both batched requests and individual fallback requests.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 477–482)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This converts a grid of cell values into simple text. It makes spreadsheet rows readable outside the spreadsheet interface.

**Data flow**: It receives any value. If the value is not a list, it returns an empty string. If it is a list of row lists, it joins cells in each row with ` | ` and joins rows with newlines.

**Call relations**: render calls this for sheet_values records. It is the last step that turns raw cell arrays into recallable body text.

*Call graph*: called by 1 (render).


##### `_str`  (lines 485–486)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns optional or unknown values into text fields. It avoids showing Python-style nulls or objects where a title string is expected.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render calls this when building titles and short bodies for spreadsheet and sheet records. It keeps display text clean even when Google data is missing or oddly shaped.

*Call graph*: called by 1 (render).


### Team communications
Communication providers import chats, channels, messages, mail, conversations, contacts, and events from Microsoft Graph and Slack.

### `extensions/sources/ufo_ext_sources/providers/microsoft_teams.py`

`io_transport` · `source sync`

This connector is the bridge between Microsoft Teams and the project’s source-sync system. Without it, the system would not know where to ask Microsoft for a user’s teams, channels, chats, or messages, and Teams content could not become recallable searchable material.

The file describes five streams of data: teams, channels, channel messages, chats, and chat messages. A stream is simply one kind of thing the sync can collect. Microsoft Graph returns long lists in pages, so the connector repeatedly follows Microsoft’s “next page” link until it has all the results. Think of it like reading a long email thread one screen at a time until there is no “next” button left.

Some data depends on other data. To get channel messages, the connector first gets teams, then each team’s channels, then each channel’s messages. To get chat messages, it first gets chats, then each chat’s messages. Messages can be synced incrementally: if the system already knows the last saved update time, this file skips older messages and only yields newer ones.

The connector is deliberately read-only. It does not post or edit Teams content. It also tries to be forgiving: if one team or chat is forbidden or missing, it skips that parent and keeps going. But if the whole Microsoft permission grant is not allowed to list teams or chats, it reports the stream as skipped rather than crashing the entire run.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 60–64)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s joined Microsoft Teams. Other parts of the connector use this as the starting point for finding channels and channel messages.

**Data flow**: It receives an HTTP client that can talk to Microsoft Graph. It asks the `/me/joinedTeams` endpoint for teams, follows all result pages, collects the team records into one list, and returns that list.

**Call relations**: When the sync wants the teams stream, `MicrosoftTeamsConnector.paginate` calls this directly. When the sync needs channels, `MicrosoftTeamsConnector._channels` calls this first so it knows which teams to inspect.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 66–79)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds channels inside each team the user belongs to. It also adds the team’s identity to each channel record, so later steps know where that channel came from.

**Data flow**: It receives an HTTP client. It first gets teams from `MicrosoftTeamsConnector._teams`, then for each valid team ID it asks Microsoft Graph for that team’s channels. Before yielding channel pages onward, it attaches context such as `team_id` and `team_name`. If one team’s channels are forbidden or missing, it skips that team and continues.

**Call relations**: This function sits between team discovery and message discovery. `MicrosoftTeamsConnector.paginate` uses it for the channels stream, and `MicrosoftTeamsConnector._channel_messages` uses it so it can visit each channel and fetch its messages.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 81–112)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from every accessible Teams channel. It can limit the results to messages changed after a saved checkpoint, which keeps repeat syncs from re-reading old messages.

**Data flow**: It receives an HTTP client and an optional cursor, where the cursor is the last known update time as text. It gets channel records from `MicrosoftTeamsConnector._channels`, asks Microsoft Graph for each channel’s messages, filters out messages whose `lastModifiedDateTime` is not newer than the cursor, and yields only non-empty pages. It adds context like `team_id`, `channel_id`, and `thread_id` so downstream storage can understand where each message belongs.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this when the requested stream is `channel_messages`. This function depends on `MicrosoftTeamsConnector._channels` to know which channel URLs to query, and it uses `with_context` to pass parent information along with each message page.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 114–118)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the signed-in user’s Microsoft Teams chats. These are separate from team channels, so they need their own discovery step.

**Data flow**: It receives an HTTP client, asks Microsoft Graph’s `/me/chats` endpoint for chat records, follows all result pages, gathers the records into one list, and returns that list.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this directly for the chats stream. `MicrosoftTeamsConnector._chat_messages` also calls it first so it knows which chats to visit for messages.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 120–140)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages from each accessible Teams chat. Like channel message syncing, it can skip messages that are not newer than the stored checkpoint.

**Data flow**: It receives an HTTP client and an optional cursor. It gets chats from `MicrosoftTeamsConnector._chats`, checks that each chat has a usable ID, asks Microsoft Graph for that chat’s messages, filters by `lastModifiedDateTime` when a cursor is present, and yields message pages with `chat_id` and `thread_id` added. If one chat is forbidden or missing, it skips that chat and keeps syncing the rest.

**Call relations**: `MicrosoftTeamsConnector.paginate` calls this for the `chat_messages` stream. This function relies on `MicrosoftTeamsConnector._chats` for the list of parent chats and uses `with_context` so the messages remain tied to their chat.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 142–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main traffic director for this connector. Given a requested stream, it chooses the right fetching routine and yields pages of records to the source-sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and routes to the matching helper: teams, channels, channel messages, chats, or chat messages. It yields pages as they become available. If Microsoft rejects access with an authorization-style error, it turns that into a `StreamSkipped` result so the run records a clean skip instead of an unexpected failure. If the stream name is unknown, it also reports the stream as skipped.

**Call relations**: This is the public paging entry used by the shared source framework. It calls the private fetching methods in this file, and it is the place where Microsoft permission failures are translated into the framework’s skip signal.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 177–183)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Microsoft Teams record into readable text for storage or search. Message records get special treatment because Microsoft stores their body as HTML rather than plain text.

**Data flow**: It receives a record and the stream it came from. For non-message streams, it delegates to the base connector’s normal rendering. For channel and chat messages, it reads the subject, extracts `body.content`, strips HTML tags, builds a simple heading, and returns both a title and a text body.

**Call relations**: The source framework calls this after records are fetched and before they become recallable pages. It uses `_str` to safely read the message subject, `get_path` to reach nested body content, and `_strip_html` to make the Teams HTML body easier to read.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 186–189)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes HTML tags from a string so a Teams message body becomes more readable as plain text. If the input is not text, it returns nothing.

**Data flow**: It receives any value. If the value is a string, it replaces HTML-like tags with spaces and trims the result. If the value is not a string, it returns `None`.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when preparing channel and chat messages. It is a small cleanup helper that keeps HTML formatting from leaking into the stored readable page.

*Call graph*: called by 1 (render).


##### `_str`  (lines 192–193)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts a possible title value into a string only when it already is one. This avoids accidentally displaying non-text values as message titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. It keeps title creation simple and predictable before the message text is assembled.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/outlook.py`

`io_transport` · `source sync run`

This connector is the bridge between the project and a user’s Outlook account. Outlook data is exposed through Microsoft Graph, Microsoft’s web API for mailbox and calendar data. The file defines which Outlook streams exist, how to page through them, how to remember progress, and how to reshape Microsoft’s raw records into friendlier fields.

The most important idea here is the “delta” feed. A delta feed is like asking a librarian, “What changed since my last visit?” On the first run, the connector walks through all available pages and receives a special link at the end. That link becomes the cursor, or bookmark. On the next run, the connector sends that bookmark back to Microsoft Graph and receives only new, changed, or deleted items.

Messages and contacts are trickier because they can live in multiple folders. For those streams, the cursor is a small JSON map from folder ID to that folder’s delta link. Conversations are not a separate Outlook object here; they are built by reading messages and grouping them by conversation ID.

The file also treats permission problems carefully. If Microsoft Graph says the account is unauthorized or missing scope, the stream is marked as skipped instead of crashing the whole run.

#### Function details

##### `_graph_instant`  (lines 45–46)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: Turns a Python date and time into the exact UTC timestamp format Microsoft Graph expects in filters. This is used when the connector asks Graph for items newer than a certain point.

**Data flow**: It receives a datetime value → converts it to UTC → formats it as text like "2024-01-01T12:00:00Z" → returns that text for use in a Graph query.

**Call relations**: When conversation or message syncing needs a starting time for a first backfill, those flows call this helper before sending a request to Microsoft Graph.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 49–52)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from text so an event description becomes easier to read. It is a small cleanup step for calendar event bodies.

**Data flow**: It receives any value → if the value is not text, it returns nothing → if it is text, it replaces HTML tags with spaces and trims the result → returns plain-looking text.

**Call relations**: The flattening step calls this when preparing event records, because Microsoft Graph may return event body content as HTML.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 55–63)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address on a contact record. Contacts may contain a list of email addresses, and this chooses a simple primary-looking value for downstream use.

**Data flow**: It receives a contact dictionary → looks at its emailAddresses list → checks each nested email address field → returns the first non-empty email address it finds, or nothing if there is none.

**Call relations**: The contact flattening flow calls this helper when turning a raw Microsoft contact into a record with a direct email field.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 66–76)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: Chooses a useful phone number from a contact record. It prefers the mobile phone number, then falls back to the first business phone number.

**Data flow**: It receives a contact dictionary → checks mobilePhone first → if that is missing, scans businessPhones → returns the first usable phone number, or nothing if no phone is available.

**Call relations**: The contact flattening flow calls this helper so the final contact record has a simple phone field instead of only Microsoft’s nested phone structure.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 129–140)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the public paging entry used by the source runner. It passes along the stream, cursor, and optional backfill floor to the connector’s main pagination logic.

**Data flow**: It receives an HTTP client, a stream description, the last saved cursor, a user ID value that this connector does not use, and an optional earliest date → forwards the relevant values to paginate → yields the pages that paginate produces.

**Call relations**: The wider source framework calls this when it wants Outlook rows. This method then hands the work to OutlookConnector.paginate, adding support for the backfill date used by mail-related streams.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 142–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the correct sync method for the requested Outlook stream. It is the traffic director for contacts, messages, conversations, events, and mail folders.

**Data flow**: It receives the stream name plus cursor and optional backfill date → routes the request to the matching stream-specific method → yields pages of records or deletion markers → if Microsoft Graph refuses access with a permission error, it turns that into a skipped stream instead of an ordinary failure.

**Call relations**: OutlookConnector.paginate_source calls this during a sync. Depending on the stream, it delegates to the conversation, message, contact, event, or generic Graph delta reader, and it raises StreamSkipped when the stream cannot be read safely.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 186–226)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds conversation records from messages. Outlook messages have conversation IDs, so this method groups messages into one record per email thread.

**Data flow**: It receives an HTTP client, a saved cursor, and an optional earliest date → asks Graph for messages ordered by last modified time → filters from the cursor or backfill date when possible → groups messages by conversationId → keeps the latest message details while preserving the earliest creation time → yields one list of conversation records.

**Call relations**: OutlookConnector.paginate calls this for the conversations stream. It uses _graph_instant when it needs to turn the backfill date into a Graph filter, and it reads message pages through the base connector’s OData paging helper.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 228–265)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: Reads a Microsoft Graph delta feed and turns it into standard stream pages. This is the shared engine for streams where Graph can report both changed records and deleted records.

**Data flow**: It receives an HTTP client, an initial Graph path, an optional cursor link, and optional query parameters → starts from the cursor if one exists, otherwise from the initial path → fetches each Graph page → separates normal records from items marked as removed → yields a StreamPage with records, deleted IDs, and the next cursor → follows next-page links until Graph gives the final delta link.

**Call relations**: OutlookConnector.paginate uses this directly for mail folders, while message, contact, and event syncing use it as their shared lower-level delta reader. It packages Graph responses into StreamPage objects so the rest of the system sees a consistent shape.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 267–290)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs Outlook messages folder by folder. This is needed because message delta cursors are tracked separately for each mail folder.

**Data flow**: It receives an HTTP client, a saved cursor map, and an optional earliest date → decodes the cursor map → lists mail folders → for each folder, starts from that folder’s saved delta link or opens a new folder delta feed → optionally applies a first-run received-date backfill filter → adds mail_folder_id to each message record → updates the folder’s cursor → yields StreamPage objects with a refreshed JSON cursor map.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. It asks _list_mail_folders for folder IDs, uses _graph_delta_pages to read each folder’s changes, and uses _decode_cursor_map and _encode_cursor_map to preserve one bookmark per folder.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 292–319)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs Outlook contacts across the default contacts area and any contact folders. Like messages, contacts may need separate cursors per folder.

**Data flow**: It receives an HTTP client and a saved cursor map → decodes the map → builds a folder list containing the default contacts area plus named contact folders → reads each folder’s delta feed → updates that folder’s cursor → yields StreamPage objects with the combined cursor map. If the default contacts delta endpoint is unavailable with certain expected errors, it skips that default area and continues.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It uses _list_contact_folders to discover folders, _graph_delta_pages to read changes, and the cursor map helpers to remember progress separately for each folder.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 321–332)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Syncs calendar events using Microsoft Graph’s calendar view delta feed. It limits the calendar window to a practical range around the current date.

**Data flow**: It receives an HTTP client and an optional cursor → calculates a window from one year in the past to two years in the future → opens or resumes the calendar delta feed → yields each StreamPage from the shared delta reader.

**Call relations**: OutlookConnector.paginate calls this for the events stream. It delegates the actual paging and deletion detection to _graph_delta_pages after setting the time window Graph requires for calendar view delta requests.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 334–341)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of the user’s Outlook mail folders. Message syncing needs these IDs so it can read each folder’s delta feed.

**Data flow**: It receives an HTTP client → requests mail folder pages from Microsoft Graph → scans each folder record for a non-empty id → returns a list of folder IDs.

**Call relations**: The message delta flow calls this before reading messages, because it must know which folders to visit and which cursor belongs to each folder.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 343–350)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Collects the IDs of custom Outlook contact folders. Contact syncing uses these IDs to read contacts outside the default contacts area.

**Data flow**: It receives an HTTP client → requests contact folder pages from Microsoft Graph → pulls out valid folder IDs → returns those IDs as a list.

**Call relations**: The contact delta flow calls this after adding the built-in default contacts area, so it can visit every contact folder that may contain records.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 352–382)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Microsoft Graph records into friendlier records with commonly used field names. It keeps the original data but adds simple fields like email, phone, subject, snippet, and event times.

**Data flow**: It receives one raw record and the stream it belongs to → checks the stream name → for contacts, adds names, email, phone, and created_at; for messages, adds subject, snippet, sender, sent time, and thread IDs; for events, adds title, plain description, start, end, and location → returns the enriched record. If the stream has no special rules, it returns the record unchanged.

**Call relations**: After pages are fetched, the connector framework can call this to normalize records before storage or indexing. It uses _first_email, _phone, _strip_html, and nested-field lookups to turn Microsoft’s nested structures into easier top-level fields.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 385–394)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: Reads a saved JSON cursor map for folder-based streams. This lets the connector remember a separate Graph bookmark for each mail or contact folder.

**Data flow**: It receives raw cursor text or nothing → if there is no text or the JSON is invalid, returns an empty map → if the JSON is a dictionary, keeps only non-empty string values → returns a clean folder-to-cursor dictionary.

**Call relations**: Message and contact syncing call this at the start of their folder loops. It turns the single saved cursor string from the framework into the per-folder bookmarks those flows need.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 397–398)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: Writes a folder cursor map back into JSON text for saving. This turns many per-folder bookmarks into the one cursor string the sync framework expects.

**Data flow**: It receives a dictionary of folder IDs to cursor links → if the dictionary is empty, returns nothing → otherwise serializes it as sorted JSON text → returns that text as the next saved cursor.

**Call relations**: Message and contact syncing call this after each folder page updates the cursor map. The resulting text is placed into StreamPage so the sync framework can store it for the next run.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/slack.py`

`io_transport` · `source sync runs`

Slack does not hand over a whole workspace in one neat package. It gives data in pages, uses special cursor tokens to ask for the next page, and often reports API problems inside a normal-looking response. This file is the adapter that understands those Slack habits.

At the top, it defines the Slack streams the system can sync: users, conversations, threads, messages, and participants. The SlackConnector then decides how to read each stream. Users and conversations are simple snapshots: ask Slack page by page, flatten each item into a stable shape, and mark missing items as deleted elsewhere in the sync system. Messages are more careful. The connector first gathers users and channels, then walks each channel’s message history separately. This matters because a busy channel should not cause a quiet channel to be skipped. Think of it like checking each mailbox with its own bookmark, instead of using one bookmark for every mailbox.

The file also converts Slack-specific details into clearer records: Unix times become ISO timestamps, Slack message timestamps become sortable bounds, user profiles are simplified, messages become searchable text records, and thread and participant records are derived from the same raw history page. It deliberately skips data the current Slack permission grant cannot read, rather than treating missing permissions as a system crash.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error when Slack says a request failed even though the HTTP request itself looked successful. It keeps Slack’s error code, and sometimes the missing permission name, so later code can decide whether to skip or fail.

**Data flow**: It receives Slack’s error text and an optional needed permission → builds a readable error message and stores the details on the exception → returns an exception object ready to be raised.

**Call relations**: _ok_or_raise calls this when Slack returns ok=false. The stored error code is then used by higher-level code to decide whether a missing permission should skip a stream or stop the run.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard source-connector entry point for paging through Slack records. It exists so the wider source-sync framework can ask Slack for pages without knowing Slack’s details.

**Data flow**: It receives an HTTP client, a stream description, a saved cursor, the current bot/user id, and an optional backfill limit → passes those inputs unchanged into the Slack-specific paginate method → returns the pages produced there.

**Call relations**: The source framework calls this method. It immediately hands control to SlackConnector.paginate, which contains the real stream-by-stream routing.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses the right reading plan for each Slack stream. It knows that users and conversations are simple lists, while message-based streams must be built by walking channel histories.

**Data flow**: It receives the requested stream and sync position information → for users or conversations, yields flattened API pages; for message streams, builds a user lookup, gathers readable non-archived channels, and runs a per-channel history walk → yields lists of records or stream pages with cursor information.

**Call relations**: SlackConnector.paginate_source calls this as the main dispatcher. It calls iter_users, iter_conversations, user_index, _slack_ts, and creates a PartitionWalk so channel history can be read safely one channel at a time. If the stream is unknown, it raises StreamSkipped.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies the channel ids that should be walked for message-based streams. It is a small helper so the partition walker can ask, one by one, which Slack channels to read.

**Data flow**: It reads the channel map already collected by paginate → yields each channel id → produces an asynchronous sequence of channel identifiers.

**Call relations**: SlackConnector.paginate passes this helper into PartitionWalk. PartitionWalk uses it as the list of separate channel “mailboxes” whose histories need their own progress tracking.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the generic partition walker to Slack’s channel-history reader. For one channel and one time window, it asks _channel_pages to fetch the actual history pages.

**Data flow**: It receives a channel id and a time bound from the partition walker → looks up that channel’s conversation details → returns an iterator over WalkPage objects for that channel.

**Call relations**: SlackConnector.paginate gives this helper to PartitionWalk. When PartitionWalk is ready to read a channel slice, this helper hands the work to SlackConnector._channel_pages.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users page by page. It turns Slack’s raw member objects into cleaner user records that the rest of the system can store consistently.

**Data flow**: It starts with no Slack cursor → calls users.list with a page size, flattens valid members, yields non-empty pages, then follows Slack’s next cursor until there is no next page → produces batches of user records.

**Call relations**: SlackConnector.paginate calls this for the users stream, and SlackConnector.user_index calls it to build a lookup table for message processing. It relies on _enumerate for safe API calls, _flatten_user for shaping records, and _next_cursor for paging.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including public channels, private channels, group messages, direct messages, and archived channels. It makes each conversation record easier to understand and compare.

**Data flow**: It asks Slack for conversations in pages → for each valid channel object, extracts names, type flags, privacy/archive status, timestamps, topic, purpose, and member count → yields batches until Slack has no next cursor.

**Call relations**: SlackConnector.paginate uses this both for the conversations stream and to find channels for message-history streams. It calls _enumerate for the API request, then helper functions to classify conversations, extract nested fields, convert times, and follow cursors.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a dictionary of Slack users keyed by user id. Message processing uses this lookup to attach readable names and email addresses to messages and participants.

**Data flow**: It reads user pages from iter_users → stores each user record under its id → returns a complete id-to-user map for the visible workspace users.

**Call relations**: SlackConnector.paginate calls this before reading message-based streams. The resulting lookup is passed down into _channel_pages and _message_page so raw messages can be enriched with user details.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one Slack channel’s message history for a particular time window. It understands Slack’s newest-first history API and turns each raw page into records for the requested message-related stream.

**Data flow**: It receives a channel, stream, time boundary, user lookup, and current bot/user id → builds Slack history request parameters, fetches pages, filters valid raw messages, converts them through _message_page, and follows Slack cursors → yields WalkPage objects that include records plus the newest and oldest timestamps in that page.

**Call relations**: PartitionWalk reaches this through the channel_pages helper inside SlackConnector.paginate. It calls _slack_post to read conversations.history, _message_page to derive records, and _next_cursor to keep paging. If Slack refuses one channel for an expected reason, it raises PartitionSkipped so other channels can still sync.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific record type requested: threads, messages, or message participants. It also notes deleted messages so the messages stream can remove them.

**Data flow**: It receives raw Slack messages, conversation details, user lookup, and the current bot/user id → skips deletion notices except to record their deleted ids, ignores messages from the connector’s own Slack user, flattens normal messages, derives possible thread and participant records, and computes the page timestamp span → returns one WalkPage containing the right record list.

**Call relations**: _channel_pages calls this after each conversations.history response. It delegates record shaping to _flatten_message, _conversation_thread_from_message, and _participant_for_message, then packages the result for PartitionWalk using WalkPage.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs a top-level Slack list request and treats missing access as a skipped stream instead of a hard failure. This is important because a Slack token may lack permission to list users or channels.

**Data flow**: It receives an API path and query parameters → calls _slack_get → if Slack or HTTP status says the grant lacks permission, converts that into StreamSkipped; otherwise returns the response data or re-raises unexpected errors.

**Call relations**: iter_users and iter_conversations use this for their list APIs. It calls _slack_get for the actual request and acts as the permission-aware safety layer around enumeration.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a Slack GET request and checks Slack’s own success flag. It hides the detail that Slack may return HTTP 200 while still saying the API call failed.

**Data flow**: It receives an HTTP client, path, and optional query parameters → uses the base connector’s GET method → passes the returned data to _ok_or_raise → returns only successful Slack data.

**Call relations**: _enumerate calls this for users.list and conversations.list. It hands Slack response checking to _ok_or_raise.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a Slack POST request and checks Slack’s own success flag. It is used for Slack APIs, such as channel history, that are read through POST requests.

**Data flow**: It receives an HTTP client, path, and optional JSON body → uses the base connector’s POST method → passes the returned data to _ok_or_raise → returns only successful Slack data.

**Call relations**: _channel_pages calls this for conversations.history. It relies on _ok_or_raise to turn Slack ok=false responses into SlackApiError.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether Slack says a response is successful. If Slack says ok=false, it raises a SlackApiError with the Slack error code.

**Data flow**: It receives decoded Slack response data → looks at the ok field → returns the data unchanged when successful, or raises SlackApiError when Slack reports a problem.

**Call relations**: _slack_get and _slack_post call this after network requests. When it raises SlackApiError, callers such as _enumerate and _channel_pages decide whether to skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s next-page token in a response. This is how the connector knows whether there is another page to request.

**Data flow**: It receives Slack response data → looks inside response_metadata.next_cursor → returns the cursor string if present and non-empty, otherwise returns None.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this after each page. A returned cursor causes another API request; None ends the paging loop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack’s whole-number Unix timestamps into ISO date strings. ISO strings are easier for the rest of the system to store, compare, and display.

**Data flow**: It receives any value → rejects booleans and values that cannot be read as seconds → converts valid seconds since 1970 into a UTC ISO timestamp string, or returns None if invalid.

**Call relations**: iter_conversations uses this for conversation creation times, and _flatten_user uses it for user update times.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a Python datetime into Slack’s message timestamp string format for history bounds. It pads the value so string comparisons match time order.

**Data flow**: It receives an optional datetime → if absent or before the Unix epoch, returns None; otherwise converts it to seconds with six decimal places and fixed width → returns a Slack-style timestamp string.

**Call relations**: SlackConnector.paginate calls this when setting the oldest allowed backfill point for PartitionWalk. That helps the channel-history walk stop descending too far into the past.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack’s message timestamp string into an ISO date string. This makes message and thread times readable outside Slack’s special format.

**Data flow**: It receives a Slack timestamp string or None → parses it as seconds since 1970 → returns a UTC ISO timestamp, or None if the value is missing or invalid.

**Call relations**: _flatten_message uses this for sent_at, and _conversation_thread_from_message uses it for thread activity times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Simplifies a raw Slack user object into the user record shape this system expects. It pulls useful profile fields into top-level fields and cleans the email address.

**Data flow**: It receives one raw Slack member dictionary → reads profile details, chooses sensible display and real names, lowercases email, converts update time, and records bot/deleted flags → returns a normalized user dictionary.

**Call relations**: iter_users calls this for each valid Slack member. It uses _first_text to choose the best available name and _unix_to_iso to format the update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into a normalized message record. It also filters out messages sent by the connector’s own Slack user so the sync does not index its own live bot output.

**Data flow**: It receives a raw message, conversation details, user lookup, and current bot/user id → validates the timestamp and channel, skips the self user, finds sender details, builds ids for the message and thread, converts sent time, creates a short snippet, and fills sender fields → returns a message dictionary or None if the message should be ignored.

**Call relations**: _message_page calls this for each non-deletion raw message. It uses _slack_ts_to_iso for time conversion, _snippet for preview text, and _first_text to choose a sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Derives a thread record from a Slack message when that message is either a thread root with replies or a reply inside a thread. Plain standalone messages do not become thread records.

**Data flow**: It receives a normalized message plus the original raw message and conversation → checks thread ids, reply counts, and latest reply time → returns a thread summary with title, counts, timestamps, privacy/archive flags, and parent channel, or None if no thread is represented.

**Call relations**: _message_page calls this after flattening each message. It uses _slack_ts_to_iso to turn Slack thread timestamps into readable created and updated times.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This lets the system later answer who took part in a message or thread.

**Data flow**: It receives a normalized message and user lookup → chooses a handle from email or Slack user id, skips the record if no handle exists, then builds a participant id and sender details → returns a participant dictionary or None.

**Call relations**: _message_page calls this for each flattened message when building the message_participants stream. It uses _first_text to choose the best available handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Classifies a Slack conversation into a simple type name: direct message, multi-person direct message, private channel, or public channel.

**Data flow**: It receives a raw Slack conversation dictionary → checks Slack’s boolean type flags in priority order → returns one plain string describing the conversation type.

**Call relations**: iter_conversations calls this while flattening Slack channel objects, so downstream records do not have to understand Slack’s many overlapping flags.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value buried inside nested dictionaries, such as a channel topic’s text. It avoids errors when Slack leaves out part of the structure.

**Data flow**: It receives a dictionary and a path of keys → walks one key at a time while the current value is still a dictionary → returns the found value, or None if the path cannot be followed.

**Call relations**: iter_conversations calls this to extract topic and purpose text from Slack’s nested conversation objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from several candidates. It is used when Slack may provide the same idea in several possible fields.

**Data flow**: It receives any number of values → scans them in order, keeping only strings with non-space content → returns the first cleaned string, or None if none are usable.

**Call relations**: _flatten_user uses this for names, _flatten_message uses it for sender handles, and _participant_for_message uses it for participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Makes a short readable preview from message text. It removes extra whitespace and caps the result so previews stay compact.

**Data flow**: It receives optional text → returns None for missing or empty text; otherwise collapses runs of whitespace into single spaces and cuts the result to the configured length → returns the cleaned snippet.

**Call relations**: _flatten_message calls this when creating the message record’s snippet field, which can then be used for display or search previews.

*Call graph*: called by 1 (_flatten_message).

# Documents, knowledge bases, and structured workspace content connectors  `stage-12.2`

This stage is the system’s set of “adapters” for structured work content. It sits behind the scenes during syncing: when a user connects an outside service, these connectors know how to visit that service, read what is allowed, and turn it into source pages that the rest of the system can search and reuse.

Each connector speaks to a different tool. Airtable finds bases, tables, and records, then presents them as synced items. Google Sheets reads spreadsheets, tabs, and rows from a Google account. Notion gathers pages, blocks, databases, comments, and user details, then flattens them into readable text. Confluence does the same for spaces, pages, blog posts, comments, groups, and audit records, converting its stored web content into plain text. Google Docs reads documents only, without changing them. Google Drive covers a wider file space, including shared drives, permissions, comments, and revisions. The Y Combinator connector brings in selected YC guidance and directory-style results, such as companies, founders, jobs, and forum posts. Together, they turn many outside work libraries into one searchable memory.

## Files in this stage

### Structured workspace data
Connectors that turn databases, spreadsheets, and structured workspace records into searchable synced content.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable data is not available as one simple list. It is more like a building directory: first you ask which buildings exist, then which rooms are inside each building, then what is stored in each room. This connector follows that shape. It starts by asking Airtable for all bases, then asks for the tables inside each base, then reads records from each table in pages of up to 100 records.

The file defines three streams of data: bases, tables, and records. A stream is a named kind of thing the sync system can ask for. Bases are the main, canonical stream. Tables and records are discovered underneath them.

When the connector reads tables or records, it adds extra context such as the base ID, table ID, and table name. That matters because a record by itself does not fully explain where it came from. The added context acts like a label on a box, so later parts of the system can trace the record back to its Airtable source.

This connector only reads from Airtable. It does not create, edit, or delete Airtable data. It relies on the shared REST connector base class for the actual HTTP requests and authentication.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases available to the connected account. A base is Airtable’s top-level container, similar to a workbook or database.

**Data flow**: It receives an HTTP client that can talk to Airtable. It asks Airtable’s metadata endpoint for bases, then pulls the list stored under the "bases" field from the response. It returns that list as plain record dictionaries.

**Call relations**: The main pagination flow calls this whenever it needs to start from the top of Airtable’s hierarchy. It uses the shared records_at helper to extract the useful list from Airtable’s response before handing the bases back to paginate.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables that belong to one Airtable base. It also tags each table with the base it came from, so the table is not separated from its parent.

**Data flow**: It receives an HTTP client and one base record. It reads the base ID from that record; if the ID is missing or invalid, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the table list, adds base ID and base name to each table, and returns the enriched table records.

**Call relations**: The pagination flow calls this after it has fetched bases. It relies on records_at to pull out the table list and with_context to attach the base information before those tables are used for table syncing or record discovery.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the records inside one Airtable table, one page at a time. It adds base and table information to every record batch so each record can be traced back to its origin.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It reads the table ID; if the ID is missing or invalid, it stops without producing anything. Otherwise it requests record pages from Airtable, following Airtable’s offset token for the next page. For every non-empty page, it adds base ID, table ID, and table name, then yields that page of records.

**Call relations**: The main pagination flow calls this while syncing the records stream, after bases and tables have already been discovered. It uses with_context before yielding each page so downstream code receives records that still know which base and table they came from.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses how to read a requested Airtable stream and yields the data in batches. This is the connector’s main read path for bases, tables, and records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. If the requested stream is bases, it fetches bases and yields them once. If the stream is tables, it fetches bases, then tables for each base, grouping tables into pages of about 100. If the stream is records, it fetches bases, then tables, then yields record pages from each table. If the stream name is unknown, it raises a clear skip signal instead of pretending the stream exists.

**Call relations**: The sync system calls paginate when it wants data for one Airtable stream. paginate then calls _bases, _tables_for_base, and _records_for_table in the right order, moving from Airtable’s broadest level down to individual records. When a stream is not implemented, it creates a StreamSkipped error so the caller can skip it cleanly.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Airtable records into a cleaner, more consistent shape for the rest of the system. It also adds useful URLs or normalized field names depending on whether the record is a base, table, or table record.

**Data flow**: It receives one record and the stream it belongs to. For bases, it keeps the record and adds a direct metadata API URL. For tables, it adds a URL for that table inside its base. For records, it normalizes the creation time into created_at and ensures fields is always a dictionary. It returns the adjusted record without changing the original stream choice.

**Call relations**: After paginate has produced raw Airtable data, the broader connector framework can call flatten to prepare each item for storage or indexing. Unlike the fetch functions, flatten does not call other project helpers here; it is the final shaping step for each record.


### `extensions/sources/ufo_ext_sources/google_sheets.py`

`io_transport` · `source sync`

This connector is a read-only bridge between the system and Google Sheets. Without it, the system could not discover a user’s spreadsheets, split them into sheet tabs, or fetch the cell values that make the spreadsheet useful as readable content.

The file works in layers. First it asks Google Drive for spreadsheet files, because Drive is where Google lists files and their modified times. That lets the connector do an incremental sync: after the first run, it can ask only for spreadsheets changed after the last saved time, instead of rereading everything. Then, for each spreadsheet file, it asks the Google Sheets API for spreadsheet details such as the title and tab list. From that one spreadsheet record, it can produce one record per tab. Finally, for each tab, it asks for the grid values so rows can be rendered as text.

A useful detail is how it treats permissions. If the whole grant lacks the needed Google permission, it marks the stream as skipped rather than crashing the whole sync. If Drive can see a spreadsheet but Sheets cannot open it, it still keeps basic Drive metadata instead of losing the item completely. The `render` method then turns raw API records into simple text, like a label on a folder plus the rows inside.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 53–83)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for each Google Sheets stream. It collects spreadsheet records, sheet-tab records, or sheet-value records into pages so the sync system can process them in manageable batches.

**Data flow**: It receives an HTTP client, a stream description, and possibly a saved cursor time. It reads spreadsheet data first, then either keeps those spreadsheet records as-is, expands them into tab records, or fetches cell values for each tab. It yields lists of records, and if Google refuses access because the needed permission is missing, it turns that into a clear “stream skipped” result instead of an ordinary failure.

**Call relations**: The sync runner calls this when it wants records for one of the connector’s streams. Inside, it relies on `GoogleSheetsConnector._spreadsheet_records` to discover spreadsheets, calls `_sheet_records` when the requested stream is sheet tabs, and calls `GoogleSheetsConnector._sheet_value_records` when the requested stream is cell values. If Google returns a permission error, it creates a `StreamSkipped` so the larger run can continue gracefully.

*Call graph*: calls 4 internal fn (__init__, _sheet_value_records, _spreadsheet_records, _sheet_records).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 85–110)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks Google Drive for spreadsheet files the user can see. It is responsible for paging through Drive’s file list and applying the “changed since last time” filter when a cursor is available.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for non-trashed Google Sheets files, adds a modified-time condition if there is a cursor, and repeatedly asks Drive for pages of file metadata. Each response’s `files` value is made safely into a list, then non-empty lists are yielded until Drive says there are no more pages.

**Call relations**: `GoogleSheetsConnector._spreadsheet_records` calls this first because Drive is the best place to list spreadsheets and their modification times. This function hands back batches of Drive file metadata, and the caller then uses each file id to ask the Sheets API for richer spreadsheet details.

*Call graph*: called by 1 (_spreadsheet_records); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_records`  (lines 112–142)

```
async def _spreadsheet_records(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function turns Google Drive spreadsheet files into full spreadsheet records. It combines Drive metadata, such as creation and update times, with Sheets metadata, such as the spreadsheet title and tab list.

**Data flow**: It receives an HTTP client and an optional cursor. It reads spreadsheet files from `GoogleSheetsConnector._iter_spreadsheet_files`, takes each valid file id, and asks the Sheets API for spreadsheet details. If Sheets refuses or cannot find a spreadsheet that Drive listed, it falls back to a minimal record using the Drive file name. It yields one enriched spreadsheet dictionary at a time, including stable ids, title, URL, created time, and updated time.

**Call relations**: `GoogleSheetsConnector.paginate` calls this as the shared starting point for all streams. The spreadsheet records it produces can be returned directly for the `spreadsheets` stream, expanded into tabs by `_sheet_records`, or used by `GoogleSheetsConnector._sheet_value_records` to fetch each tab’s rows.

*Call graph*: calls 1 internal fn (_iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 144–166)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This function reads the actual cell rows for every tab in a spreadsheet. It produces the records that make a synced sheet recallable as content, not just as metadata.

**Data flow**: It receives an HTTP client and one spreadsheet record. It looks through the spreadsheet’s tab list, skips tabs without a usable title or id, safely encodes the tab title for use in a web address, and asks the Sheets API for that tab’s values by row. It yields one value record per tab, including the spreadsheet id, spreadsheet title, sheet id, sheet title, and the returned grid values.

**Call relations**: `GoogleSheetsConnector.paginate` calls this when the requested stream is `sheet_values`. It depends on spreadsheet records already gathered by `GoogleSheetsConnector._spreadsheet_records`, then hands back row data that can later be rendered by `GoogleSheetsConnector.render`.

*Call graph*: called by 1 (paginate); 1 external calls (quote).


##### `GoogleSheetsConnector.render`  (lines 168–187)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a raw Google Sheets record into readable text. It gives each spreadsheet, tab, or value grid a useful title and body so the rest of the system can store or display it as plain content.

**Data flow**: It receives a record and the stream it came from. For spreadsheets, it extracts the spreadsheet title and lists tab names. For sheet tabs, it shows the tab title and parent spreadsheet. For sheet values, it turns the grid rows into pipe-separated text. It returns a pair: a short title and a formatted text body.

**Call relations**: The broader source framework calls this after records have been fetched and need to become human-readable content. It uses `_str` to safely read optional text fields and `_grid_text` to convert a cell grid into lines of text. If it sees an unknown stream, it hands the work back to the base connector’s renderer.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_sheet_records`  (lines 190–209)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper splits one spreadsheet record into separate records for its sheet tabs. It lets the system treat each tab as its own item instead of only seeing the whole spreadsheet.

**Data flow**: It receives one spreadsheet dictionary. It reads the spreadsheet id and walks through the spreadsheet’s `sheets` list, ignoring malformed entries and tabs without an id. For each valid tab, it creates a new record with a combined id, the parent spreadsheet id and title, and the tab title, then returns the full list.

**Call relations**: `GoogleSheetsConnector.paginate` calls this when it is serving the `sheets` stream. It sits between whole-spreadsheet discovery and per-tab syncing, turning one parent record into several child records.

*Call graph*: called by 1 (paginate).


##### `_grid_text`  (lines 212–217)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This helper converts spreadsheet cell values into simple readable text. It makes rows look like plain lines, with cells separated by vertical bars.

**Data flow**: It receives an unknown value that should be a list of rows. If the value is not a list, it returns an empty string. Otherwise, it keeps row-shaped lists, converts each cell to text, joins cells with ` | `, joins rows with newlines, and returns the final text block.

**Call relations**: `GoogleSheetsConnector.render` calls this when rendering the `sheet_values` stream. The helper keeps the rendering code simple by doing the grid-to-text conversion in one place.

*Call graph*: called by 1 (render).


##### `_str`  (lines 220–221)

```
def _str(value: Any) -> str
```

**Purpose**: This helper safely turns optional values into text fields. It prevents non-string values, such as missing data or numbers in the wrong place, from leaking into titles and headings.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: `GoogleSheetsConnector.render` calls this while building titles, headings, and short body text. It acts like a small safety filter before records become human-readable output.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `source sync and record rendering`

Notion stores work as nested documents, not as simple rows in a table. A page title may be hidden inside a property, paragraph text may live inside a block, and comments and users have their own shapes. Without this connector, the system would mostly see raw Notion JSON, which is hard for people and search tools to understand.

The file defines a NotionConnector, which knows which Notion streams are available: users, pages, data sources, comments, and blocks. During a sync, it creates an HTTP client with the Notion API version header, then chooses the right way to read each stream. Pages and data sources are found through Notion search. Users come from the users endpoint. Comments are fetched page by page. Blocks are fetched by walking each page’s block tree, like opening folders inside folders, with a depth limit so it cannot wander forever.

The connector also understands incremental syncing. When given a saved cursor, it keeps only records newer than that cursor. If Notion refuses access because the integration lacks permission, the stream is marked as skipped rather than crashing the whole run.

Finally, render turns Notion’s structured fields into readable prose: titles, property values, block text, checklist marks, comment text, names, and emails.

#### Function details

##### `NotionConnector._make_client`  (lines 69–72)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Notion. It adds the required Notion API version header so Notion knows which version of its rules the connector expects.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to build the client, then adds the Notion-Version header. It returns the ready-to-use client.

**Call relations**: This is part of the connector setup before requests are made. Later pagination and collection-reading functions use the client it prepares to make Notion API calls.


##### `NotionConnector.paginate`  (lines 74–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Notion stream. Given a requested stream, it chooses the correct reader and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and passes record batches outward. If Notion returns an access-denied response, it changes that into a clean “stream skipped” result.

**Call relations**: The sync framework calls this when it wants records from Notion. It hands work to _collection for users, _search for pages and data sources, _comments for comments, and _blocks for page body blocks. If a stream is unknown or inaccessible, it uses StreamSkipped so the larger sync can continue safely.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 106–129)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Notion search results for either pages or data sources. It sorts them by edit time and filters out records that are not newer than the saved cursor.

**Data flow**: It receives an HTTP client, the Notion object type to search for, and an optional last-seen edit time. It repeatedly posts search requests, pulls the results list out of each response, drops old records when a cursor is present, and yields non-empty batches. It stops when Notion says there are no more pages of results.

**Call relations**: paginate calls this directly for pages and data sources. _blocks and _comments also use it first to discover pages, because blocks and comments are fetched by starting from each page.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 131–141)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the body blocks that make up Notion pages. It starts from pages, then walks into each page’s block children so the system can capture page content, not just page metadata.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads all pages through _search, takes each page ID, then asks _block_children to fetch that page’s blocks and nested blocks. It yields batches of block records that pass the cursor filter inside the child walker.

**Call relations**: paginate calls this when the requested stream is blocks. It relies on _search to find pages and delegates the recursive tree-walking work to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 143–162)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the children of a Notion block or page. It is what lets the connector capture nested content such as lists inside toggles or paragraphs inside sections.

**Data flow**: It receives an HTTP client, a block ID, a current depth, and an optional cursor. It stops if the depth is beyond the safety limit. Otherwise it fetches child blocks, yields the ones newer than the cursor, and then repeats the process for child blocks that are allowed to be opened further.

**Call relations**: _blocks calls this for each page ID. The function uses _collection to fetch each page of child blocks, then calls itself for deeper children, while avoiding child pages, child databases, and AI blocks because those are treated separately or should not be expanded here.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 164–180)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Notion pages. It makes comments searchable alongside page content.

**Data flow**: It receives an HTTP client and an optional cursor. It finds pages through _search, uses each page ID to request comments, filters out comments older than the cursor when needed, and yields any remaining comment batches.

**Call relations**: paginate calls this when syncing the comments stream. It uses _search to find pages first and _collection to page through Notion’s comment responses for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 182–196)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged list of results. It hides the repeated work of following Notion’s next-cursor paging system.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST helper to fetch result pages using Notion’s result and cursor field names. It yields each batch of records as it arrives.

**Call relations**: paginate uses this directly for users. _block_children uses it for block children, and _comments uses it for page comments. It is the common doorway for simple GET-style Notion collections.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 198–218)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion records into readable text. It exists because Notion content is stored in nested fields, and a plain JSON dump would not read like the original page, comment, block, or user profile.

**Data flow**: It receives one Notion record and the stream it came from. It picks the right text extractor for that stream, builds a title and body, then returns both the title and a Markdown-like text block with a heading. For unknown streams, it falls back to the parent connector’s rendering behavior.

**Call relations**: The sync system uses this after records are fetched, when it needs human-readable content. It calls helpers such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text to pull text out of Notion’s different record shapes.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 221–222)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into text only when it is already a string. It prevents unexpected objects or missing values from becoming confusing output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: render and several text helpers use this whenever Notion may or may not provide a plain string. It keeps _property_text, _block_text, and _user_text from having to repeat the same safety check.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 225–233)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts readable text from Notion’s rich text format. Notion stores formatted text as a list of pieces, and this joins the plain text from those pieces.

**Data flow**: It receives a possible rich-text value. If it is a list, it reads the plain_text field from each valid piece, joins the pieces together, trims extra space, and returns the result. If the input is not a list, it returns an empty string.

**Call relations**: render uses this for comments and data source fields. _page_title, _property_text, and _block_text also call it whenever they need to turn Notion rich-text runs into normal prose.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 236–245)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the title of a Notion page. Page titles are stored as a special property, so this helper searches the properties until it finds that title field.

**Data flow**: It receives a page record. It looks inside the page’s properties, finds the property whose type is title, extracts its rich text, and returns the first non-empty title it finds. If there is no usable title, it returns an empty string.

**Call relations**: render calls this when turning a page record into readable text. It relies on _rich_text_text because Notion titles use the same rich-text piece format as other formatted text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 248–257)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into readable lines. It makes database-style fields such as status, dates, people, or checkboxes show up as plain text.

**Data flow**: It receives a page record. It looks through the page’s properties, asks _property_text to convert each supported property value, and formats non-empty values as “name: value” lines. It returns all lines joined with newlines.

**Call relations**: render calls this for page records after finding the page title. It delegates the details of each property type to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 260–279)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property into a simple string. It knows the common property shapes people actually read, such as titles, rich text, selections, people, dates, links, numbers, emails, phone numbers, and checkboxes.

**Data flow**: It receives a property dictionary. It checks the property’s type, pulls the matching value field, and converts supported types into text. Unsupported or malformed properties become an empty string.

**Call relations**: _properties_text calls this once for each page property. It uses _rich_text_text for formatted text fields and _str for string fields such as names and dates.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 282–292)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from a Notion block. Blocks are the building pieces of a page, such as paragraphs, headings, to-do items, child pages, or child databases.

**Data flow**: It receives a block record. It finds the block’s type-specific content, then returns the title for child page or database blocks, the joined rich text for normal text blocks, or a checkbox-style line for to-do blocks. If the block has no readable content, it returns an empty string.

**Call relations**: render calls this for records in the blocks stream. It uses _rich_text_text for normal block text and _str when child page or database titles are simple strings.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 295–298)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion user record into readable profile text. It includes the user’s name and email address when available.

**Data flow**: It receives a user record. It looks for the top-level name and the nested person email, keeps whichever values are real strings, and joins them on separate lines. Missing values are simply left out.

**Call relations**: render calls this for user records. It uses _str to avoid showing non-string or missing fields as misleading text.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Collaborative document repositories
Connectors that ingest collaborative document and file repositories, converting pages, files, comments, and metadata into searchable records.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `sync run`

Confluence does not store page bodies as simple text. It stores them as XHTML, which is like HTML with extra Confluence-specific tags for macros, links, and page structure. If the system saved that raw markup, a recalled page would look noisy and hard to read. This file fixes that by fetching Confluence records and reshaping them into useful, human-readable records.

The connector first asks Atlassian which Confluence sites the current permission grant can access. A single grant may cover more than one site, so it loops through each site and reads the requested stream, such as pages or comments. It uses Atlassian’s paged API, asking for small batches and continuing while Confluence says there is another page of results. For streams that can be synced incrementally, it compares each record’s date-like cursor field with the saved watermark and keeps only newer records.

Before records leave the connector, `flatten` gives them stable fields like title, body, URL, author, and creation time. It also prefixes most record IDs with the Confluence site ID, so two different sites cannot accidentally produce the same ID. Finally, `render` turns pages, blog posts, comments, and space descriptions into plain text. The helper parser acts like someone copying only the visible words from a web page while adding line breaks where paragraphs, headings, and table cells naturally separate.

#### Function details

##### `_body_text`  (lines 103–105)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: This helper finds the readable body content inside a Confluence record. It prefers the stored page body, and falls back to the viewed body if that is what the record contains.

**Data flow**: It receives one record as a dictionary-like object. It looks for `body.storage.value`, then `body.view.value`, and returns the first non-empty string it finds. If neither path contains useful text, it returns nothing.

**Call relations**: During record cleanup, `ConfluenceConnector.flatten` calls this helper when preparing pages, blog posts, and comments. The helper relies on `get_path` so it can safely read deeply nested fields without crashing when a part is missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 113–139)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Confluence stream. It finds every accessible Confluence site, reads the requested kind of data from each site, and yields batches of records for the rest of the sync process.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It maps the stream name to the right Confluence API path, asks `_sites` which sites are available, then asks `_offset_results` for batches from each site. Before yielding each batch, it adds site context such as the cloud ID and site URL. If Confluence refuses access with a permission error, it turns that into a skipped stream instead of a failed run.

**Call relations**: The sync framework calls this method when it needs records from a Confluence stream. This method delegates site discovery to `_sites`, batch fetching and cursor filtering to `_offset_results`, and uses `with_context` to attach site information that later steps, such as `flatten`, need.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 141–145)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function asks Atlassian which Confluence sites the current authorization grant can reach. It matters because Confluence API calls must be made separately for each site.

**Data flow**: It receives an HTTP client. It calls Atlassian’s accessible-resources endpoint, reads the JSON response if there is one, and returns it as a list. If the response is missing or not shaped like a list, it safely returns an empty list.

**Call relations**: `ConfluenceConnector.paginate` calls this before reading any stream data. The returned site IDs become the `cloud_id` values used to build site-specific Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 147–171)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: This function walks through a Confluence collection one page of API results at a time. It also performs local incremental filtering when a saved cursor is available.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and optional cursor information. It requests records with `start` and `limit` values, extracts the `results` list, filters out records older than or equal to the saved cursor when needed, and yields non-empty batches. It stops when there are no kept records or Confluence does not provide a next-page link.

**Call relations**: `ConfluenceConnector.paginate` calls this for each accessible Confluence site. This method uses `records_at` to pull records from the API response and `get_path` to check both cursor values and the `_links.next` continuation marker.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 173–221)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw Confluence API records into cleaner records with fields the rest of the system expects. It also makes record IDs unique across Confluence sites.

**Data flow**: It receives one raw record and its stream description. Depending on the stream, it copies useful fields into standard names such as `title`, `body`, `url`, `created_at`, `updated_at`, `author`, and `parent_external_id`. It builds web URLs from the site URL and Confluence web link when possible. For most streams, it prefixes the primary key with the site’s cloud ID, then lifts nested cursor values into a flat key so watermark tracking can work.

**Call relations**: The sync pipeline calls this after records are fetched. It calls `_body_text` for page-like content and `get_path` for nested values such as version creation time. Its output is what later storage and rendering steps see.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 223–237)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a flattened Confluence record into a title and a readable text document. It is especially important for pages, blog posts, comments, and spaces because their bodies may still contain Confluence XHTML markup.

**Data flow**: It receives a record and its stream description. For pages, blog posts, and comments, it takes the title and extracts plain text from the body. For spaces, it uses the space name or key and extracts text from the description. For other streams, it lets the base connector render them normally. It returns a short title plus a text block headed with the stream name.

**Call relations**: The broader source system calls this when it needs recallable prose from a synced record. It uses `_StorageTextExtractor.extract` to clean markup, `_str` to safely handle optional titles, and `get_path` to read nested space descriptions.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 246–248)

```
def __init__(self) -> None
```

**Purpose**: This sets up a small HTML parser that will collect only the readable text parts of Confluence storage markup. It starts with an empty list of text pieces.

**Data flow**: It creates a parser configured to automatically turn HTML character references, such as `&amp;`, into normal characters. It initializes an internal list where later parser callbacks will store words and line breaks.

**Call relations**: `_StorageTextExtractor.extract` creates an instance of this parser before feeding it Confluence body markup. The later parser methods add text into the list prepared here.


##### `_StorageTextExtractor.extract`  (lines 251–256)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: This is the simple public entry point for turning Confluence XHTML into plain text. Callers use it when they want visible words without tags and attributes.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise, it creates a parser, feeds the raw markup into it, asks the parser to assemble the cleaned text, and returns that text.

**Call relations**: `ConfluenceConnector.render` uses this for page bodies, blog post bodies, comments, and space descriptions. Inside the parser run, `handle_data`, `handle_starttag`, and `handle_endtag` collect the raw pieces, and `_text` performs the final cleanup.


##### `_StorageTextExtractor.handle_data`  (lines 258–259)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This parser callback keeps the visible text found between HTML tags. It is how actual page words make it into the final output.

**Data flow**: It receives a chunk of text from the parser. It appends that chunk to the parser’s internal list without changing it at this stage. The cleaned version is produced later.

**Call relations**: The built-in HTML parser calls this automatically while `_StorageTextExtractor.extract` feeds it markup. The collected chunks are later joined and normalized by `_StorageTextExtractor._text`.


##### `_StorageTextExtractor.handle_starttag`  (lines 261–263)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This parser callback adds a line break when a block-like HTML tag begins. It helps the final text keep natural separation between paragraphs, headings, table cells, and list items.

**Data flow**: It receives a tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the internal list. It ignores attributes completely, because they are not useful readable text.

**Call relations**: The built-in HTML parser calls this during `_StorageTextExtractor.extract`. Its newline markers are later cleaned up by `_StorageTextExtractor._text` so the output has readable spacing instead of raw markup.


##### `_StorageTextExtractor.handle_endtag`  (lines 265–267)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This parser callback adds a line break when a block-like HTML tag ends. Together with start-tag handling, it preserves paragraph-like structure in the plain text.

**Data flow**: It receives a tag name. If the tag is a known block tag, it appends a newline marker to the internal list. Tags that do not represent visible block boundaries are ignored.

**Call relations**: The built-in HTML parser calls this while processing markup passed through `_StorageTextExtractor.extract`. The line breaks it adds are folded into clean text by `_StorageTextExtractor._text`.


##### `_StorageTextExtractor._text`  (lines 269–272)

```
def _text(self) -> str
```

**Purpose**: This function turns the parser’s collected pieces into final clean text. It removes messy extra spacing while keeping meaningful line breaks.

**Data flow**: It joins all collected text and newline pieces into one string. It splits that string into lines, compresses repeated whitespace inside each line, drops empty lines, and returns the result with leading and trailing whitespace removed.

**Call relations**: `_StorageTextExtractor.extract` calls this after feeding all markup to the parser. It is the final cleanup step after `handle_data`, `handle_starttag`, and `handle_endtag` have collected the raw pieces.


##### `_str`  (lines 275–276)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns an optional value into a string only when it already is one. It prevents titles from accidentally becoming representations of non-text values.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string.

**Call relations**: `ConfluenceConnector.render` calls this when choosing titles for pages, blog posts, comments, and spaces. It keeps rendering simple and avoids surprising output when Confluence leaves a title missing or uses an unexpected type.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/google_docs.py`

`io_transport` · `source sync`

This connector is the bridge between Google Docs and the rest of the system’s source-sync pipeline. Its job is to find Google Docs through Google Drive, fetch each document’s full contents through the Google Docs API, and shape the result into records the system can store and later search or recall.

It works in two stages. First, it asks Google Drive for files whose type is “Google Doc,” skipping trashed files and, when possible, only asking for files changed since the last sync. That stored “last seen modified time” is the cursor, like a bookmark in a long list. Drive returns files in pages, so the connector keeps following Google’s next-page token until there is nothing left.

Second, for each file Drive lists, it asks the Docs API for the full document. If a document is visible in Drive but cannot be opened, the connector keeps a small stub instead of failing the whole sync. This matters because one restricted or deleted document should not block every other document.

Finally, the file includes rendering logic that walks Google’s nested document structure and pulls out paragraph text. Google stores document text in small pieces inside a tree; this code flattens those pieces into the prose a person would expect to read.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 49–85)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the Google Docs stream. It gathers changed Google Docs, fetches each document, adds useful Drive metadata, and yields records in batches so the sync system can process them without holding everything forever.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor saying where the previous sync left off. It asks Drive for matching document files, fetches each full document by file id, combines the document body with file details such as title, URL, creation time, and modified time, then outputs lists of document records. If Google refuses access to the listing as a whole, it turns that into a skipped stream rather than an unexpected crash.

**Call relations**: During a sync, the wider source framework calls this method to get document pages. It relies on GoogleDocsConnector._iter_doc_files to find candidate files, then calls GoogleDocsConnector._document for each file it can identify. If access is refused at the stream level, it raises StreamSkipped so the larger run can continue with a clear explanation.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 87–96)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one Google Doc in full from the Google Docs API. It protects the sync from failing just because a single listed file cannot be opened or has disappeared.

**Data flow**: It receives an HTTP client and a Google Drive file id. It uses that id to request the document contents from Google, then returns the document data as a dictionary. If Google says the document is forbidden or not found, it returns a minimal record containing only the document id; other errors are passed upward.

**Call relations**: GoogleDocsConnector.paginate calls this after Drive has found a document file. The result goes back into paginate, which combines it with Drive metadata so even incomplete documents can still produce a usable sync record.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 98–125)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through Google Drive’s file list and yields pages of Google Docs the account can see. It uses the cursor to ask Google for only documents modified after the last successful sync.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for non-trashed Google Docs, adds the cursor condition when present, requests one page of files at a time, cleans the returned file list into a safe list, and yields each non-empty page. It keeps following Google’s page token until there are no more pages.

**Call relations**: GoogleDocsConnector.paginate uses this as its file-finding step. This function hands batches of Drive file metadata upward, and paginate then decides which file ids are valid and fetches their matching document bodies.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 127–132)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one synced Google Docs record into a title and a plain-text body suitable for display, indexing, or recall. It adds a simple heading so the text is clearly labeled as coming from Google Docs.

**Data flow**: It receives a document record and the stream description. It reads the title if one exists, asks _plain_text to extract readable text from the document body, builds a heading that includes the connector and stream name, and returns the title plus the final text block.

**Call relations**: The source framework uses this after records have been fetched, when it needs a human-readable version of the document. render delegates the hard part of flattening Google’s nested document body to _plain_text, then wraps that text with context.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 135–151)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts the visible paragraph text from a Google Docs document record. It turns Google’s nested document format into ordinary text.

**Data flow**: It receives a document record. It looks inside the body content, visits each paragraph, collects the text from each text run, joins all those pieces in order, trims extra space at the ends, and returns one plain string. Parts of the document that are not paragraphs, such as some structural elements, are skipped.

**Call relations**: GoogleDocsConnector.render calls this when preparing a document for readable output. It does not call back into the network or the sync system; it is the final text-cleanup step after the document data has already been fetched.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/google_drive.py`

`io_transport` · `during source sync runs`

This connector is the system’s read-only doorway into Google Drive. Without it, the project could not pull in Drive file metadata, notice when files are deleted or trashed, or attach related details like comments and permissions.

The main class, GoogleDriveConnector, describes several streams of data. The most important stream is files. On the first run, it lists all untrashed files and then asks Google for a “start page token,” which is like a bookmark saying, “next time, continue from here.” On later runs, it uses that bookmark to read only changes. If Google says the bookmark is too old, the connector raises a special CursorExpired signal so the wider sync system can start fresh instead of silently missing data.

Shared drives are simpler: the connector rereads the full list each time. Permissions, comments, and revisions are child data, so the connector first walks through every file, then asks Google for that file’s child records.

The file also deals carefully with permission problems. If the connected account does not have enough Google Drive access, the stream is marked as skipped rather than failed. Finally, the render method turns a file record into a small readable text block with its name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 71–103)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading each Google Drive stream. It decides whether to fetch files, file changes, shared drives, or per-file child records like comments and permissions.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the saved bookmark from a previous sync. It chooses the right helper, yields pages of records or special stream pages, and turns broad Google Drive access refusals into a StreamSkipped result. The output is a sequence of pages that the sync engine can store, update, or skip.

**Call relations**: The wider source-sync machinery calls this when it wants data for one stream. For the files stream, it either calls _paginate_file_changes when there is already a cursor, or _paginate_files followed by _start_page_token on a first sync. For shared drives it calls _paginate_shared_drives, and for permissions, comments, or revisions it calls _paginate_file_children.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 105–130)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Google Drive file records in pages. It can list all live, untrashed files, or only files modified after a given time.

**Data flow**: It starts with an optional cursor and builds a Google Drive search query. It repeatedly asks the Drive files endpoint for a page, converts the returned files value into a safe list, yields that list when it has records, and follows Google’s next-page token until there are no more pages.

**Call relations**: paginate uses this during the first files sync. _paginate_file_children also uses it as the starting list of files before it asks for each file’s permissions, comments, or revisions.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 132–137)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current changes bookmark. The connector uses that bookmark so the next sync can read only what changed after the first full file listing.

**Data flow**: It sends a request to Google’s startPageToken endpoint. It reads the startPageToken field from the response and returns it only if it is a real non-empty string; otherwise it returns nothing.

**Call relations**: paginate calls this after it finishes the first full files listing. The token it returns is wrapped in a StreamPage so the larger sync system can save it as the next cursor.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 139–184)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads the Google Drive change log after a previous sync. It finds updated files, removed files, and trashed files so the local copy can stay in step with Drive.

**Data flow**: It receives a saved change token and uses it as the current page token. For each Google changes page, it separates normal file records from deletions: removed or trashed files become delete IDs, while live file objects become records. It yields StreamPage objects containing records, deletes, and the next cursor. If Google reports that the token has expired, it raises CursorExpired so the system can do a fresh sync.

**Call relations**: paginate calls this whenever the files stream already has a cursor. It hands StreamPage results back to the sync engine, including both upserts and tombstones, which tell the engine which records should be removed.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 186–203)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches the list of shared drives visible to the connected Google account. Shared drives are reread in full rather than tracked through a change cursor.

**Data flow**: It starts with no page token, asks the Google drives endpoint for one page at a time, turns the returned drives field into a safe list, yields records when present, and follows nextPageToken until Google has no more pages.

**Call relations**: paginate calls this when the requested stream is shared_drives. It is a self-contained reader for that stream and hands pages directly back to the caller.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 205–243)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches records that live under individual files, such as permissions, comments, and revisions. It works file by file because Google exposes these as sub-lists attached to each file.

**Data flow**: It first reads all current files using _paginate_files. For each file with a valid ID, it calls the matching child endpoint, follows child-page tokens, filters by the child stream’s cursor field when a cursor is provided, and adds the parent file ID and name to each returned child record. If Google refuses access to a particular child list or the file is not available, it skips that one file’s child data and continues.

**Call relations**: paginate calls this for the permissions, comments, and revisions streams. This helper depends on _paginate_files to know which files to inspect, then yields enriched child records back to the sync flow.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 245–260)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a short human-readable text body. That text gives later search or recall features something understandable to display or index.

**Data flow**: It receives a raw record and the stream it came from. For non-file streams, it lets the base connector render normally. For file records, it extracts the name, MIME type, owners, and web link, then returns a title and a formatted text block.

**Call relations**: The larger connector framework calls this when it needs a readable version of a synced record. For file records it uses the local _str helper to safely turn the file name into text; for all other streams it hands rendering back to the parent RestConnector.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 263–264)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only when it is already text. It prevents non-string values from accidentally becoming confusing titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when it needs the file name as a safe title. It keeps rendering simple and predictable even when Google returns missing or oddly shaped data.

*Call graph*: called by 1 (render).


### Curated external knowledge
Connectors that import selected external guidance and directory-style knowledge collections into the workspace.

### `extensions/yc/ufo_ext_yc/source.py`

`io_transport` · `tool request handling and source sync`

This file is the bridge between UFO’s source-sync system and YC’s search tools. Without it, the workspace could not safely import YC guidance or a chosen slice of Bookface-style search results into shared memory.

The file first defines the allowed YC collections and the small data shapes used to validate requests and parse responses. A source configuration says which collection to sync, whether a search query is needed, how many results to keep, and how often to refresh. The validation rules matter because guidance collections are already curated and do not accept a query, while directory collections would be too broad without one.

The main worker is `YcSource`. When UFO asks it to fetch data, it checks a cursor, which is a saved timestamp from the last sync. If the data is still fresh, it skips work. Otherwise it calls the YC command runner page by page, asks for CSV-formatted results, checks that the reply is consistent, and turns each row into a `Page` object. Each page gets a stable reference, a hash digest so changes can be detected, and a body trimmed to a safe size limit. The public `yc_index` tool lets a user request a bounded YC search to be synced into shared workspace memory.

#### Function details

##### `YcSourceConfig.validate_collection`  (lines 79–88)

```
def validate_collection(self) -> 'YcSourceConfig'
```

**Purpose**: This checks that a YC source configuration makes sense before UFO uses it. It prevents confusing or unsafe combinations, such as giving a search query to a fixed guidance collection or trying to search a directory collection without a query.

**Data flow**: It starts with a partly built configuration containing a collection name, optional query, optional result limit, and refresh timing. It compares the collection against the known guidance and search collections, fills in the default result limit for directory searches when needed, and returns the cleaned configuration. If the combination is invalid, it stops construction with a clear error.

**Call relations**: This validator runs automatically when `YcSourceConfig` is created, including when `yc_index` builds a source request and when stored source configuration is loaded for syncing. It acts like a gatekeeper before `YcSource.fetch` ever tries to contact YC.


##### `YcSource.fetch`  (lines 138–155)

```
async def fetch(self, config: YcSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main entry point UFO uses when it wants fresh YC pages for a workspace source. It avoids unnecessary work by honoring the saved refresh time, and it turns connection problems into a clean “skip this stream” result instead of a crash.

**Data flow**: It receives a validated source configuration, an optional cursor string from the previous sync, and authentication context for the workspace. It reads the cursor timestamp, compares it with the current time and the configured refresh interval, and either returns no new pages or asks `_fetch_collection` to collect them. It outputs a `SyncResult` containing the pages, a new cursor timestamp, and a marker that this is a full snapshot.

**Call relations**: The source-sync system calls this function when syncing the YC backend. If a refresh is needed, it hands the detailed fetching work to `YcSource._fetch_collection`. It uses a timeout so a slow YC call cannot hang forever, and if the YC credentials slot is not connected, it raises `StreamSkipped` so the wider sync can continue gracefully.

*Call graph*: calls 2 internal fn (__init__, _fetch_collection); 5 external calls (__init__, __init__, timeout, now, timedelta).


##### `YcSource._fetch_collection`  (lines 157–213)

```
async def _fetch_collection(self, config: YcSourceConfig, auth: SourceAuth) -> tuple[Page, ...]
```

**Purpose**: This gathers one YC collection by repeatedly asking the YC runner for pages of search results. It enforces result limits and checks that YC’s response matches what was requested.

**Data flow**: It takes the source configuration and workspace auth information. It builds a compact JSON request for each page, either adding the special guidance filter or adding the user’s search query, then sends that request through the YC runner. The raw JSON reply is validated, the embedded CSV is converted into `Page` objects by `_pages`, and pages are accumulated until the collection is exhausted or the requested maximum is reached. It returns the final tuple of pages.

**Call relations**: `YcSource.fetch` calls this only when a sync is due. Inside the loop, this function uses `_pages` to translate YC CSV into UFO pages; `_pages` then chooses the correct row parser for guidance or directory data. This function is the safety checkpoint between the external YC tool and UFO’s internal source store.

*Call graph*: calls 1 internal fn (_pages); called by 1 (fetch); 1 external calls (dumps).


##### `YcSource._pages`  (lines 215–218)

```
def _pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This chooses the right way to turn YC CSV text into UFO pages. Guidance rows and directory-search rows have different column layouts, so they need different parsers.

**Data flow**: It receives a collection name and the CSV body returned by YC. It checks whether the collection is one of the fixed guidance collections, then sends the CSV to either `_guidance_pages` or `_directory_pages`. It returns the pages produced by that specialized parser.

**Call relations**: `YcSource._fetch_collection` calls this after each successful YC search response. This function is the small switchboard that hands the raw result text to the correct converter before the pages flow back into the sync result.

*Call graph*: calls 2 internal fn (_directory_pages, _guidance_pages); called by 1 (_fetch_collection).


##### `YcSource._guidance_pages`  (lines 220–236)

```
def _guidance_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This converts YC guidance search results, such as user manuals or startup library entries, into UFO source pages. It keeps the useful human-readable fields and gives each page a stable identity and digest.

**Data flow**: It receives the collection name and a CSV string. It reads each CSV row, validates the expected fields, joins the link, description, body, and categories into one readable text block, and sends that text through `_bounded` so it cannot exceed the size limit. For each row it creates a `Page` with a source reference, a SHA-256 digest, and the final body text, then returns all pages as a tuple.

**Call relations**: `YcSource._pages` calls this when the collection is a guidance collection. It relies on `_bounded` to keep page size safe before handing the finished `Page` objects back through `_pages` to `_fetch_collection`.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 4 external calls (__init__, DictReader, sha256, StringIO).


##### `YcSource._directory_pages`  (lines 238–267)

```
def _directory_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This converts YC directory-style search results into UFO source pages. These rows can have many different columns, so it preserves all non-empty fields except the basic ID and link.

**Data flow**: It receives the collection name and a CSV string. It reads each row, pulls out the record ID and link, collects the remaining non-empty columns as name-value lines, and builds a readable page body. The body is trimmed by `_bounded` if needed, then wrapped in a `Page` with a stable source reference and a SHA-256 digest. The output is a tuple of pages ready for syncing.

**Call relations**: `YcSource._pages` calls this for searchable directory collections such as companies, founders, deals, jobs, and forum. Like the guidance converter, it uses `_bounded` before returning pages to the fetch loop.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 4 external calls (__init__, DictReader, sha256, StringIO).


##### `YcSource._bounded`  (lines 269–274)

```
def _bounded(self, body: str) -> str
```

**Purpose**: This keeps a page body below the maximum byte size allowed for YC source pages. It protects the sync system from very large records while leaving a clear note when text has been cut off.

**Data flow**: It receives a text body and encodes it as bytes, because storage limits are measured in bytes rather than characters. If the body is small enough, it returns it unchanged. If it is too large, it keeps only the prefix that fits, safely decodes it back to text, appends a truncation notice, and returns the shortened body.

**Call relations**: Both `_guidance_pages` and `_directory_pages` call this just before creating each `Page`. It is the final size guard between YC result text and UFO’s source storage.

*Call graph*: called by 2 (_directory_pages, _guidance_pages).


##### `yc_index`  (lines 277–300)

```
async def yc_index(ctx: ToolContext, args: YcIndexInput) -> ToolResult
```

**Purpose**: This is the user-facing tool function that starts syncing a bounded YC directory search into shared workspace memory. A user can ask for something like matching companies or founders, and this registers that search as a reusable source.

**Data flow**: It receives a tool context and structured arguments: the YC entity to search, the query, the result limit, and an optional user description. It checks that the YC extension context exists, confirms the YC credentials are available, builds a `YcSourceConfig`, and registers the source under the shared subject for the workspace. It returns a short text message telling the user that the sync has been requested and that repeating the same request is safe.

**Call relations**: The tool system calls this when a user invokes `yc_index`. It creates the configuration that will later be validated and used by `YcSource.fetch` during source syncing. Its result is a `ToolResult` containing `TextContent`, so the caller gets an immediate human-readable confirmation while the source machinery does the actual indexing work.

*Call graph*: 3 external calls (__init__, __init__, __init__).

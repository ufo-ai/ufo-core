# Knowledge, document, and database source connectors  `stage-12.5`

This stage is shared behind-the-scenes support for bringing outside knowledge into the system. It is like a set of adapters for different filing cabinets. Each adapter knows how to open one service, read what the account is allowed to see, and turn it into steady streams of text and records that the rest of the system can sync, store, search, or update later.

The Airtable connector reads bases, tables, and records, translating Airtable’s layered structure into pages of data. The Confluence connector reads spaces, pages, blog posts, comments, groups, and audit records, then makes the content searchable without changing Confluence. The Google Docs connector reads allowed documents and converts them to plain text. The Google Drive connector covers files, shared drives, permissions, comments, and revisions, and helps decide what to store, update, delete, skip, or render. The Google Sheets connector finds spreadsheets, opens their tabs and cells, and formats them as readable content. The Notion connector reads users, pages, databases, comments, and blocks, turning workspace content into searchable prose.

## Files in this stage

### Airtable databases
Reads Airtable bases, tables, and records as paged streams for later syncing and search.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable does not offer one simple “give me everything” endpoint. Its data is arranged like a set of filing cabinets: first you find the bases, then each base’s tables, then each table’s records. This connector walks that structure for the rest of the system.

The file defines three readable streams: bases, tables, and records. During a sync, the connector first asks Airtable for all bases. For tables, it loops through every base and asks for that base’s table list. For records, it loops through every base and table, then reads the records page by page. Airtable uses an “offset” token, which is like a bookmark saying where the next page starts.

As it reads, the connector adds helpful context. A table is stamped with the base it came from. A record is stamped with both its base and table. Without that, a record would be like a loose sheet of paper with no clue which filing cabinet or folder it belonged to.

The connector is read-only. It does not create or update Airtable data. Its job is to safely fetch Airtable content, shape it into predictable records, and hand those records to the wider source-sync system.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases available to the connected account. A base is Airtable’s top-level container, similar to a workbook or project space.

**Data flow**: It receives an HTTP client that is already ready to talk to Airtable. It asks Airtable’s metadata endpoint for bases, then pulls the actual base list out of the response. It returns a plain list of base records for later steps to use.

**Call relations**: This is the first step used by pagination. When the connector needs bases, tables, or records, paginate calls this helper so every later lookup starts from the current list of available bases.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables inside one Airtable base. It also labels each table with the base it came from, so the table is not separated from its parent context.

**Data flow**: It receives an HTTP client and one base record. It reads the base’s id; if the id is missing or not usable, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the table list, adds the base id and base name to each table, and returns the enriched table records.

**Call relations**: paginate calls this after it has found bases. In the tables stream, it gathers tables from every base. In the records stream, it uses these table records to know which record endpoints to read next.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads records from one Airtable table, page by page. It adds the base and table information to each record so downstream readers can tell exactly where the record came from.

**Data flow**: It receives an HTTP client, a base id, and one table record. It checks that the table has a usable id. If not, it stops without producing anything. If the id is valid, it repeatedly asks Airtable for record pages using Airtable’s offset bookmark and a page size of 100. For each non-empty page, it adds base id, table id, and table name to the records, then yields that page onward.

**Call relations**: paginate calls this only when syncing the records stream. It is the final step in the Airtable walk: after bases and tables are discovered, this helper pulls the actual row-like records and hands each page back to paginate.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Decides how to read each Airtable stream and yields the data in pages. It is the main routing point for turning a requested stream name into the right Airtable API walk.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. It looks at the stream name. For bases, it fetches bases and yields them once. For tables, it fetches all bases, gathers their tables, and yields table pages of about 100 items. For records, it fetches bases, then tables, then record pages for each table. If the stream name is not one this connector knows, it raises a skip signal instead of pretending it can read it.

**Call relations**: The wider sync system calls paginate when it wants data for one Airtable stream. paginate then calls the smaller helpers in the needed order: bases first, tables next, and records last. It is the coordinator that turns Airtable’s nested layout into a stream of pages the rest of the system can consume.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Airtable records into a steadier shape for storage and indexing. It keeps the original data but adds or normalizes a few fields that make records easier to identify and use later.

**Data flow**: It receives one record and the stream it belongs to. For a base, it keeps the record and adds a direct metadata API URL. For a table, it adds a direct table API URL using the stored base id. For a record, it normalizes the created time into created_at and ensures fields is a dictionary, falling back to an empty one if Airtable sent something unexpected. It returns the cleaned-up record without changing the input object in place.

**Call relations**: After paginate has yielded raw pages, the source framework can call flatten on each item before saving or exposing it. This function does not fetch more data; it prepares each fetched item so later parts of the system see predictable names and useful links.


### Confluence knowledge base
Extracts Confluence spaces, pages, posts, comments, groups, and audit data into readable searchable text.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `during Confluence source sync`

Confluence stores page bodies as storage-format XHTML, which is a structured HTML-like format full of tags and macros. If the system saved that raw markup, a person searching later would see noisy technical text instead of the page as a teammate would read it. This connector fixes that by fetching Confluence records, reshaping them into the common fields the sync system expects, and rendering pages, posts, comments, and space descriptions as plain prose.

The connector talks to Atlassian through its OAuth API. A single permission grant can cover several Confluence sites, so the connector first asks Atlassian which sites are reachable, then reads each stream separately for each site. It adds the site id to each record id, like putting a building name before a room number, so two sites with the same page id do not collide.

Confluence does not offer a simple “give me only changes since last time” option for these streams. So for incremental streams, this file still pages through results and filters out anything older than the saved cursor. If Confluence refuses access because the grant lacks permission, the stream is marked skipped rather than treating the whole run as broken.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: This helper finds the readable body markup inside a Confluence record. It prefers the stored body format, then falls back to the view body if that is what the record contains.

**Data flow**: It receives one record as a dictionary-like object. It looks inside nested fields for body text, checks that the result is a non-empty string, and returns that string; if there is no usable body, it returns nothing.

**Call relations**: When ConfluenceConnector.flatten is shaping pages, blog posts, or comments into the system’s common record form, it calls this helper so the body field is filled from the right nested Confluence location.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for a Confluence stream. It finds every Confluence site the user granted access to, reads the requested stream from each site, and yields batches of records for the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It maps the stream name to the correct Confluence API path, asks for accessible sites, reads paged results for each site, adds site context such as cloud id and site URL to each batch, and yields those batches onward. If access is refused with a permission-related status, it turns that into a controlled skip instead of an unexpected failure.

**Call relations**: The sync framework calls this when it needs records for a stream. It relies on ConfluenceConnector._sites to discover sites, ConfluenceConnector._offset_results to walk through pages of API results, and with_context to attach site information before handing records back to the framework.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Confluence sites the current authorization grant can reach. That matters because every later Confluence API request must be scoped to a specific site id.

**Data flow**: It uses the HTTP client to call Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure it is treated as a list even if the response is empty or oddly shaped, and returns a list of site records.

**Call relations**: ConfluenceConnector.paginate calls this before reading any stream. The returned site ids become the cloud ids that paginate uses to build each site-specific Confluence API path.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: This walks through one Confluence collection a page at a time. It also filters out older records when the sync is continuing from a previous cursor.

**Data flow**: It receives an API path, optional query parameters, and optional cursor information. It repeatedly requests records using Confluence’s start-and-limit paging style, extracts the result list, removes records whose cursor value is not newer than the saved cursor, and yields non-empty batches. It stops when there are no records to yield or Confluence no longer provides a next-page link.

**Call relations**: ConfluenceConnector.paginate calls this for each reachable site and stream. This function does the lower-level paging work, using records_at to pull out the results list and get_path to inspect nested cursor and next-link fields.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This converts raw Confluence API records into the flatter, more consistent shape the rest of the system expects. It adds useful fields such as title, body, URL, dates, author, and parent id depending on the stream.

**Data flow**: It receives one raw record and its stream description. It reads nested Confluence fields, builds a flatter dictionary, prefixes most ids with the Confluence site id to avoid cross-site clashes, and lifts nested cursor values to the cursor key used by the sync engine. It returns the reshaped record.

**Call relations**: After records are fetched, the connector framework uses this method to normalize them before storage or rendering. It calls _body_text for page-like bodies and get_path for nested values such as version dates, links, and authors.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Confluence records into text that is pleasant to search and read. It avoids dumping raw JSON or raw XHTML markup for pages, blog posts, comments, and spaces.

**Data flow**: It receives a flattened record and stream description. For page-like content it chooses a title and runs the body through the storage text extractor; for spaces it uses the name or key and extracts text from the description. It returns a pair: the title and a rendered text block with a simple heading plus the cleaned body. For other streams, it falls back to the parent connector’s rendering.

**Call relations**: The sync system calls this when it needs the recallable text version of a record. It uses _str to safely read possible titles, get_path for nested descriptions, and _StorageTextExtractor.extract to strip Confluence markup down to readable words.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: This prepares a small HTML parser used to collect readable text from Confluence’s storage-format XHTML. It also enables automatic conversion of HTML entities, so things like encoded ampersands become normal characters.

**Data flow**: It starts with no input besides the new object being created. It initializes the base parser and creates an empty list where pieces of text and line breaks will be collected.

**Call relations**: _StorageTextExtractor.extract creates an instance of this parser whenever it needs to clean one raw Confluence body or description.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: This is the easy entry point for turning Confluence XHTML into plain text. Callers use it when they want readable words without tags, attributes, or macro syntax.

**Data flow**: It receives any raw value. If the value is not a non-empty string, it returns an empty string. Otherwise it creates a parser, feeds the raw markup into it, asks the parser to assemble cleaned text, and returns that result.

**Call relations**: ConfluenceConnector.render calls this for pages, blog posts, comments, and space descriptions. During parsing, the HTML parser automatically calls handle_data, handle_starttag, and handle_endtag, then extract finishes by calling _text.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records the actual words found between markup tags. It is the part that keeps the content a person would read.

**Data flow**: The HTML parser passes in a text fragment. The function appends that fragment to the parser’s internal list so it can later be joined into the final plain text.

**Call relations**: This is called automatically by Python’s HTML parser while _StorageTextExtractor.extract feeds it Confluence markup. Its collected fragments are later cleaned and joined by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This notices the start of block-like HTML tags, such as paragraphs, headings, table cells, and list items, and adds a line break. That keeps separate ideas from being mashed together.

**Data flow**: The HTML parser passes in a tag name and its attributes. If the tag is one of the known block tags, the function adds a newline marker to the internal text parts; otherwise it ignores the tag and all attributes.

**Call relations**: This is called automatically during _StorageTextExtractor.extract. The newline markers it adds are later interpreted by _StorageTextExtractor._text to produce clean line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This notices the end of block-like HTML tags and adds a line break. It helps preserve the rough reading shape of the original Confluence page.

**Data flow**: The HTML parser passes in a closing tag name. If that tag is a known block tag, the function appends a newline marker; otherwise it does nothing.

**Call relations**: This runs automatically while _StorageTextExtractor.extract parses markup. Its line breaks combine with text captured by handle_data and are cleaned up by _StorageTextExtractor._text.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: This assembles the collected text fragments into the final readable string. It removes extra spaces and blank lines while keeping useful line breaks.

**Data flow**: It reads the parser’s stored parts, joins them, splits them around newline markers, collapses repeated whitespace inside each line, removes empty lines, and returns the cleaned text.

**Call relations**: _StorageTextExtractor.extract calls this after all markup has been parsed. It is the final cleanup step that turns the parser’s rough notes into plain prose.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is already a string. It prevents titles or names from accidentally becoming non-text values in rendered output.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this when choosing titles from records. That lets render build headings safely even when Confluence omits a field or sends a value of an unexpected type.

*Call graph*: called by 1 (render).


### Google workspace files
Covers Google document, drive, and spreadsheet connectors that discover files and render their contents into searchable text.

### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `source sync`

This connector is the bridge between the project and Google Docs. Its job is to find the Google Docs available through a user's Google grant, fetch their contents, and reshape them into records the rest of the system can sync and recall later.

It works in two steps. First, it asks Google Drive for files whose type is “Google document,” skipping trashed files and only asking for files changed after the last saved sync point when there is one. This is like checking a library catalog for books updated since your last visit instead of walking every aisle again. Drive returns files in pages, because there may be many.

Second, for each Drive file, it asks the Google Docs API for the full document. If one listed file cannot be opened or has disappeared, the connector keeps going and creates a small stub record instead, so a single bad document does not break the whole sync. But if Drive itself refuses access, the stream is skipped because the connector cannot even list the documents.

The final records include both Drive details, such as the web link and modified time, and Docs details, such as the document body. The render path then walks Google Docs’ nested document structure and extracts paragraph text into readable prose.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the Google Docs stream. It gathers changed Google Docs in batches, combines Drive file information with full document content, and yields pages of records for the sync system to store.

**Data flow**: It receives an HTTP client, the stream description, and an optional cursor that marks the last synced update time. It asks `_iter_doc_files` for Drive file pages, fetches each document through `_document`, builds one combined record per file, and groups records into pages of up to 100. It outputs those pages as an asynchronous stream; if Google refuses the whole listing because of missing permission, it raises `StreamSkipped` so the rest of the run can continue without this stream.

**Call relations**: During a sync, the framework calls this method to pull Google Docs records. It relies on `_iter_doc_files` to find candidate files and `_document` to fetch each file’s body. When access is broadly refused, it hands control back to the sync framework by raising `StreamSkipped` with a human-readable reason.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one Google Doc in full from the Google Docs API. It also protects the sync from failing when a listed document cannot actually be opened.

**Data flow**: It receives an HTTP client and a Google file ID. It sends a request to the Docs API for that ID and returns the document data if Google allows access. If Google says the document is forbidden or missing, it returns a minimal record containing only the document ID; other errors are left to fail normally.

**Call relations**: `paginate` calls this once for every Drive file it decides to sync. Its result is folded into the larger record that includes Drive metadata, so even unreadable or vanished documents can be represented without stopping the batch.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists Google Docs files through the Google Drive API, one page at a time. It narrows the search to non-trashed Google Docs and, when possible, only files changed since the previous sync.

**Data flow**: It receives an HTTP client and an optional cursor time. It builds a Drive search query, adds the cursor as a `modifiedTime` filter if present, requests a page of file metadata, safely turns the returned `files` value into a list, yields that list when it is not empty, then follows Google’s `nextPageToken` until there are no more pages. Its output is a sequence of file-metadata lists.

**Call relations**: `paginate` uses this as the first stage of the sync pipeline: find the documents before fetching their bodies. It uses `list_or_empty` to avoid crashing if Google returns no usable file list, then hands each page back to `paginate` for document fetching.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a title and a readable text block. The result is what the system can show or index as plain prose.

**Data flow**: It receives one document record and the stream description. It reads the title if present, asks `_plain_text` to extract the document body text, creates a simple heading that names the source and stream, and returns both the title and the final formatted text.

**Call relations**: After records have been fetched and stored, the broader source system can call this method when it needs a human-readable version of a Google Doc. It delegates the tricky part, unpacking Google’s nested body format, to `_plain_text`.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts ordinary paragraph text from the nested structure used by the Google Docs API. It turns the document body into the kind of continuous text a person expects to read.

**Data flow**: It receives a document record. It looks for `body.content`, walks each paragraph, then collects the text from each text run, which is a small stretch of characters inside a paragraph. It joins those pieces in order, trims extra whitespace at the ends, and returns one plain string; non-paragraph items such as tables or section breaks are ignored if they do not contain paragraph text in this shape.

**Call relations**: `render` calls this when building the readable output for a synced document. It does not call back into Google or change the record; it only translates the already-fetched document structure into text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `sync run`

This connector is the read-only bridge between Google Drive and the project’s source-sync system. Without it, the system would not know how to ask Google Drive for a user’s files, notice later changes, or collect extra details attached to each file.

The main class, GoogleDriveConnector, defines several streams. The most important stream is files. On the first run, it lists every untrashed Drive file the grant can see, then asks Google for a “start page token,” which is like a bookmark saying, “next time, continue watching changes from here.” On later runs, it uses that bookmark to read Drive’s changes feed. New or changed files become records. Removed or trashed files become delete markers, so the local copy can be cleaned up too. If Google says the bookmark is too old, the connector raises a special “cursor expired” signal so the wider system can start fresh instead of trusting stale data.

Shared drives are simpler: they are fully re-read each time. Permissions, comments, and revisions are gathered by first walking through all files, then asking Google for each file’s child collection. If access is denied because the grant lacks the right Drive permission, the connector marks the stream as skipped rather than failed. It also includes a small render method that turns a file record into a short human-readable summary with name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading Google Drive streams. Given a stream name, it chooses the right way to fetch that kind of data, such as files, shared drives, permissions, comments, or revisions.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the saved place from a previous sync. It checks which stream is being requested, then yields pages of records or special stream pages with cursor and delete information. If Google refuses access with an authorization-style error, it turns that into a clean “stream skipped” result instead of letting the whole sync look broken.

**Call relations**: The wider sync system calls this when it needs pages for a Google Drive stream. For files, it either hands off to _paginate_file_changes when there is an existing cursor, or to _paginate_files followed by _start_page_token on a first run. For shared drives, it hands off to _paginate_shared_drives. For permissions, comments, and revisions, it hands off to _paginate_file_children.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists Google Drive files in pages. It is used to get the full visible file set, and it can optionally filter to files modified after a given time.

**Data flow**: It starts with a blank Google page token and builds a Drive file search for untrashed files. It sends requests to Google Drive, extracts the files list safely, yields non-empty batches, then follows Google’s nextPageToken until there are no more pages. The output is a sequence of file-record lists.

**Call relations**: paginate calls this during the first files sync. _paginate_file_children also calls it so it can discover which files need child data, such as comments or permissions. It relies on list_or_empty to treat missing or malformed lists as empty instead of crashing.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the current changes-feed bookmark. That bookmark lets the next sync read only what changed after the initial full file listing.

**Data flow**: It sends one request to Google’s startPageToken endpoint. It reads the startPageToken value from the response and returns it only if it is a non-empty string. If Google does not provide a usable token, it returns nothing.

**Call relations**: paginate calls this after the first full files listing is finished. The returned token is wrapped in a StreamPage so the core sync system can save it as the next cursor.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads Google Drive’s changes feed for files after a saved cursor. It is how later syncs avoid re-reading every file from scratch.

**Data flow**: It receives a saved changes token and repeatedly asks Google for change pages. For each change, it separates live file records from deleted or trashed file IDs. It yields StreamPage objects containing changed records, delete markers, and the next cursor. If Google reports that the token has expired, it raises CursorExpired so the system knows to refresh from the beginning.

**Call relations**: paginate calls this for the files stream whenever a cursor already exists. It creates StreamPage results that carry both upserts and deletions back to the sync core, and it uses list_or_empty to safely read Google’s changes list.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This lists the shared drives visible to the connected Google account. Shared drives are read as a full list each run rather than through the changes feed.

**Data flow**: It starts without a page token, asks Google for shared-drive pages, extracts the drives list, yields each non-empty batch, then follows nextPageToken until Google says there are no more pages. The result is a sequence of shared-drive record lists.

**Call relations**: paginate calls this when the requested stream is shared_drives. It uses list_or_empty to make the response parsing tolerant of missing drive lists.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers per-file sub-data, such as permissions, comments, or revisions. It works by visiting each file first, then asking Google for the chosen child collection attached to that file.

**Data flow**: It first reads all visible files through _paginate_files. For each file with a valid ID, it builds the matching child endpoint and follows that endpoint’s pages. It filters child records by cursor when the stream supports a modified-time cursor, adds the parent file ID and file name to each child record, and yields batches of enriched child records. If Google says a specific file’s child data is forbidden or not found, it skips that file’s child collection and continues.

**Call relations**: paginate calls this for permissions, comments, and revisions. This function depends on _paginate_files to know which files to visit, and it uses list_or_empty when reading each child collection from Google’s response.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into a short readable text preview. It gives the rest of the system something useful to show or recall, rather than only raw JSON fields.

**Data flow**: It receives a record and its stream description. For non-file streams, it delegates to the parent connector’s rendering behavior. For file records, it reads the name, MIME type, owners, and web link, then returns a title plus a compact text body containing those details.

**Call relations**: The sync or indexing layer calls this when it needs a human-readable version of a record. For file titles, it calls the helper _str so a missing or non-text name becomes an empty string instead of an unsafe value.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into text only if it is already a string. It prevents non-string data from accidentally being used as a file title.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when preparing the title for a file record. It keeps the rendering path simple and predictable when Google’s response is missing a name or contains an unexpected type.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `source sync`

This connector is the bridge between the project and Google Sheets. Without it, the system could not discover a user's spreadsheets, notice which ones changed, or turn rows and tabs into content that can be synced and later recalled.

The file defines three streams, which are three ways of looking at the same Google Sheets data: whole spreadsheets, individual sheet tabs, and the cell values inside each tab. The connector first asks Google Drive for spreadsheet files, because Drive knows which spreadsheet documents exist and when they were last modified. It can use a saved time marker, called a cursor, so later syncs only ask for spreadsheets changed since the last run. Then, for each spreadsheet file, it asks the Google Sheets API for richer spreadsheet details, such as tab names.

If the account does not have permission to use Drive or Sheets, the connector marks that stream as skipped instead of crashing the whole sync. If Drive can list a spreadsheet but Sheets cannot open it, it falls back to the Drive metadata so the system still gets a useful record.

Finally, the render step turns raw API data into plain text: spreadsheet titles, tab names, and rows joined in a simple table-like format. The connector only reads data; it does not write to Google Sheets.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 54–84)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the connector's streams. It gathers spreadsheet, tab, or cell-value records and yields them in batches so the rest of the sync system can process them without loading everything at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor time. It reads spreadsheet records, then either keeps them as whole-spreadsheet records, expands them into tab records, or fetches each tab's cell values. It outputs lists of records up to the configured page size, and if Google refuses access because of missing permissions, it turns that into a clean skipped-stream signal.

**Call relations**: The sync runner calls this when it needs data for one Google Sheets stream. It relies on GoogleSheetsConnector._spreadsheet_records to get the base spreadsheet list, uses _sheet_records when the requested stream is tabs, and uses GoogleSheetsConnector._sheet_value_records when the requested stream is cell grids. If Google reports a permission problem, it raises StreamSkipped so the larger run can record a skip instead of treating it as a broken connector.

*Call graph*: calls 4 internal fn (__init__, _sheet_value_records, _spreadsheet_records, _sheet_records).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 86–111)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asks Google Drive for spreadsheet files the user can access. It is responsible for paging through Drive results and applying the saved cursor so incremental syncs only fetch recently changed spreadsheets.

**Data flow**: It receives an HTTP client and an optional cursor. It builds a Drive search query for untrashed Google Sheets files, adds a modified-time filter when a cursor exists, and repeatedly requests pages from Drive. Each response's file list is normalized with list_or_empty, then yielded as a batch; the next-page token decides whether more Drive pages need to be fetched.

**Call relations**: GoogleSheetsConnector._spreadsheet_records calls this first because Drive is the source of the spreadsheet file list. This function hands batches of Drive file metadata upward, and _spreadsheet_records then enriches each file by asking the Sheets API for spreadsheet details.

*Call graph*: called by 1 (_spreadsheet_records); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_records`  (lines 113–143)

```
async def _spreadsheet_records(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This turns Drive file entries into full spreadsheet records. It combines Drive metadata, such as creation and modification times, with Sheets metadata, such as the spreadsheet title and tab list.

**Data flow**: It receives an HTTP client and an optional cursor. It gets spreadsheet files from GoogleSheetsConnector._iter_spreadsheet_files, skips entries without a usable id, and asks the Sheets API for each spreadsheet's metadata. If Sheets refuses or cannot find one spreadsheet, it falls back to the Drive name and id. It yields one cleaned spreadsheet dictionary at a time, including id, title, URL, created time, and updated time.

**Call relations**: GoogleSheetsConnector.paginate calls this for every stream, because spreadsheets are the starting point for whole-spreadsheet records, tab records, and cell-value records. It delegates the Drive listing work to GoogleSheetsConnector._iter_spreadsheet_files, then hands enriched spreadsheet records back to paginate for stream-specific treatment.

*Call graph*: calls 1 internal fn (_iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 145–167)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> AsyncIterator[dict[str, Any]]
```

**Purpose**: This reads the actual rows and cells for each tab in a spreadsheet. It creates one record per sheet tab's grid so tab contents can become searchable text.

**Data flow**: It receives an HTTP client and one spreadsheet record. It looks through the spreadsheet's tab list, keeps tabs with a valid title and sheet id, safely encodes the tab title for use in a web address, and asks the Sheets API for rows of values. It yields a record containing the returned values plus identifiers and titles that tie the grid back to its spreadsheet and tab.

**Call relations**: GoogleSheetsConnector.paginate calls this when the requested stream is sheet_values. The function depends on spreadsheet metadata already gathered by GoogleSheetsConnector._spreadsheet_records, then hands back value records that paginate batches for the sync system.

*Call graph*: called by 1 (paginate); 1 external calls (quote).


##### `GoogleSheetsConnector.render`  (lines 169–188)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts Google Sheets records into readable text. That text is what a person, search index, or recall system can understand more easily than raw Google API data.

**Data flow**: It receives one record and the stream it came from. For spreadsheet records, it makes a heading and lists tab names. For sheet-tab records, it names the tab and its parent spreadsheet. For cell-value records, it turns rows into plain text lines. It returns a title and a rendered body string.

**Call relations**: The broader source framework calls this after records have been fetched and need to be represented as content. It uses _str to safely extract strings and _grid_text to turn cell grids into text. If the stream is not one this connector knows about, it falls back to the parent connector's rendering behavior.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_sheet_records`  (lines 191–210)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This breaks a spreadsheet record into separate records for each sheet tab. It lets the system treat each tab as its own syncable item instead of only seeing the whole spreadsheet.

**Data flow**: It receives one spreadsheet dictionary. It reads the spreadsheet id and loops through the spreadsheet's sheet list, skipping anything malformed or missing a sheet id. For each valid tab, it creates a new dictionary with a stable id, spreadsheet information, and the tab title, then returns the full list of tab records.

**Call relations**: GoogleSheetsConnector.paginate calls this when the stream being synced is sheets. It receives spreadsheet records produced by GoogleSheetsConnector._spreadsheet_records and transforms them into tab-level records for batching.

*Call graph*: called by 1 (paginate).


##### `_grid_text`  (lines 213–218)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This turns a Google Sheets cell grid into simple plain text. It makes rows readable by joining cells with vertical bars, like a lightweight table.

**Data flow**: It receives any value, usually the rows returned by the Sheets API. If the value is not a list, it returns an empty string. Otherwise, it keeps list-shaped rows, converts each cell to text, joins cells in a row with " | ", and joins rows with new lines.

**Call relations**: GoogleSheetsConnector.render calls this for sheet_values records. It provides the final human-readable body for a tab's cell contents.

*Call graph*: called by 1 (render).


##### `_str`  (lines 221–222)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns optional or unknown values into strings only when they already are strings. It prevents headings and titles from accidentally displaying Python values such as None.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise, it returns an empty string. It does not modify anything else.

**Call relations**: GoogleSheetsConnector.render calls this while building titles, headings, and short body text. It is a small safety helper that keeps rendered output clean when API fields are missing or not text.

*Call graph*: called by 1 (render).


### Notion workspace content
Reads Notion users, pages, databases, comments, and page blocks as plain searchable prose.

### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `source sync runs`

Notion stores information in a shape that is easy for its app to use but not easy for a recall/search system to read directly. A page title, a paragraph, a checklist item, or a comment may be buried inside nested fields called “rich text” runs rather than sitting in one simple text column. This connector is the translator. It knows which Notion API endpoints to call, how to page through long result sets, and how to turn Notion records into the words a human would actually see.

The file defines the streams that can be synced: users, pages, data sources, comments, and blocks. Pages and data sources are found through Notion search. Blocks are fetched by walking down each page’s block tree, like opening folders inside folders, with a depth limit so it cannot wander forever. Comments are fetched for each page. Users come from Notion’s users list.

It also treats permission problems carefully. If a Notion integration is not allowed to read a certain kind of content, the connector marks that stream as skipped instead of crashing the whole sync. Finally, its render helpers extract readable titles, property values, checklist markers, user emails, and other useful text so the stored result is useful for later recall.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Notion and adds the Notion API version header. Notion requires this header so it knows which version of its API rules the connector expects.

**Data flow**: It receives a base URL and a credential object. It asks the parent connector to build the normal authenticated client, then adds the Notion-Version header. It returns that prepared client for later API calls.

**Call relations**: This is part of the setup path before any Notion request is made. The rest of the connector’s fetching methods rely on the client it prepares so every request speaks the expected Notion API version.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching method for each Notion stream and yields records in pages. It is the main dispatcher for reading users, pages, data sources, comments, and blocks.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor that marks the last synced time. Based on the stream name, it calls the matching helper and passes along pages of records. If Notion refuses access with a 401 or 403 status, it turns that into a skipped stream rather than a failed run.

**Call relations**: The sync framework calls this when it wants records for a stream. It hands work to _collection for users, _search for pages and data sources, _comments for comments, and _blocks for page body blocks. It raises StreamSkipped when a stream is unknown or Notion says the integration lacks permission.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Searches Notion for pages or data sources and walks through all result pages. It also filters out old records during incremental syncs, because Notion search does not support a server-side “changed since this time” option here.

**Data flow**: It receives the HTTP client, the type of Notion object to search for, and an optional cursor timestamp. It posts a search request sorted by last edit time, reads the results list, removes records at or before the cursor if a cursor was provided, yields any remaining records, and continues while Notion provides a next cursor.

**Call relations**: paginate calls this directly for page and data source streams. _blocks and _comments also call it to first find the pages whose block children or comments should be inspected. It uses list_or_empty so missing or oddly shaped result lists become a safe empty list.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the visible block content inside every Notion page. Blocks are the pieces that make up a page body, such as paragraphs, headings, lists, code blocks, and to-do items.

**Data flow**: It receives the HTTP client and an optional cursor timestamp. It first searches for all pages, then for each page with a valid ID it asks _block_children to walk that page’s block tree. It yields batches of block records that are new enough for the cursor.

**Call relations**: paginate calls this for the blocks stream. This function uses _search to find pages and then hands each page ID to _block_children, which does the recursive walk through nested blocks.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the child blocks under one Notion block or page, then continues into nested child blocks when it is safe to do so. This is how the connector captures page body text that is not present in the page record itself.

**Data flow**: It receives a block ID, a current depth count, and an optional cursor timestamp. It stops if the depth is beyond the maximum. Otherwise, it fetches children from Notion, yields children edited after the cursor, and then repeats the same process for child blocks that themselves have children, except for block types that should not be descended into.

**Call relations**: _blocks starts this process for each page. This function calls _collection to fetch one level of children at a time, then calls itself recursively for nested blocks, like following branches of a tree.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches comments attached to Notion pages. Comments are handled separately because Notion exposes them through a comments endpoint for each page or block.

**Data flow**: It receives the HTTP client and an optional cursor timestamp. It searches for pages, skips anything without a usable page ID, requests comments for each page, filters out comments created at or before the cursor, and yields non-empty batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages first, then _collection to page through each page’s comments from Notion.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides one common way to read Notion endpoints that return a paged collection of results. A paged collection means Notion sends one batch at a time, plus a cursor telling where to continue.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the parent connector’s cursor paging helper to repeatedly call the endpoint, read the results field, follow next_cursor using start_cursor, and yield each batch of records.

**Call relations**: paginate uses this for users. _block_children uses it for block children, and _comments uses it for comments. It centralizes Notion’s GET-style pagination so those callers do not each need to repeat the same cursor logic.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns raw Notion API records into a title and a readable text body. This matters because raw Notion JSON is full of nested fields, while the recall system needs words that resemble what a person saw in Notion.

**Data flow**: It receives a record and the stream it came from. For pages it extracts a page title and property text; for data sources it extracts title/name and description; for blocks it extracts block text; for comments it extracts comment rich text; for users it extracts name and email. It then builds a short heading and returns the title plus the final prose.

**Call relations**: The broader sync pipeline calls render when it needs a human-readable version of a fetched record. This function delegates the small extraction jobs to _page_title, _properties_text, _rich_text_text, _block_text, _str, and _user_text, and falls back to the parent renderer for unknown streams or missing titles.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only when it is actually a string. It prevents accidental text like None, numbers, or dictionaries from being treated as meaningful prose.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render and several text helpers use this as a small safety filter. _property_text, _block_text, and _user_text call it when pulling optional names, titles, or emails out of Notion records.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: Extracts readable text from Notion’s rich text format. Rich text is Notion’s list of text pieces, where each piece can include formatting, links, and a plain_text value.

**Data flow**: It receives a value that should be a list of rich text pieces. If it is not a list, it returns an empty string. If it is a list, it joins together the plain_text fields from valid pieces, trims extra space at the ends, and returns the result.

**Call relations**: render uses this directly for comments and data source descriptions. _page_title, _property_text, and _block_text also use it whenever Notion stores visible text as rich text runs.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: Finds the visible title of a Notion page. In Notion, a page title is stored as one of the page properties rather than as a simple top-level field.

**Data flow**: It receives a page record. It looks inside the page’s properties, searches for the property whose type is title, extracts its rich text, and returns the first non-empty title it finds. If the structure is missing or no title is found, it returns an empty string.

**Call relations**: render calls this when rendering page records. It relies on _rich_text_text to turn Notion’s title rich text pieces into one plain string.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: Turns a Notion page’s properties into readable lines such as “Status: In progress” or “Owner: Alice”. This gives the recall system useful page metadata, not just the page title.

**Data flow**: It receives a page record. It checks that the properties field is a dictionary, then asks _property_text to extract a readable value for each supported property. It returns the non-empty results joined with line breaks.

**Call relations**: render calls this for page records after finding the page title. It delegates the details of each property type to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: Extracts plain text from one Notion page property, depending on what kind of property it is. Different property types store their visible value in different shapes.

**Data flow**: It receives one property dictionary. It reads the property type, finds the matching value field, and converts supported types into text: titles and rich text are joined, select/status values use their name, multi-select and people values become comma-separated names, dates use the start date, and simple values like numbers or checkboxes become strings. Unsupported or malformed properties return an empty string.

**Call relations**: _properties_text calls this once per page property. This function uses _rich_text_text for rich text fields and _str for optional string fields such as names and dates.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: Extracts the visible text from a Notion block. Blocks are the page-body building blocks, such as paragraphs, headings, child page links, databases, and to-do items.

**Data flow**: It receives a block record. It looks up the block’s type-specific content, returns titles for child pages or child databases, extracts rich text for ordinary text-like blocks, and adds “[x]” or “[ ]” in front of to-do items to preserve whether the task was checked.

**Call relations**: render calls this for records from the blocks stream. It uses _rich_text_text for the main block words and _str for child page or database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: Builds a small readable profile for a Notion user, using the user’s name and email when available. This makes user records useful in recall instead of leaving them as raw API data.

**Data flow**: It receives a user record. It reads the top-level name and, if the user has a person section, the email address. It returns the non-empty pieces joined with a line break.

**Call relations**: render calls this for the users stream. It uses _str to safely ignore missing or non-string name and email values.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).

# Concrete Source Connectors  `stage-14.1`

This stage is the system’s large intake shelf for read-only source connectors. It runs during the main sync work, after accounts are connected, and its job is to fetch data from outside services without changing it. Each connector is an adapter: it knows one provider’s web API, asks for records, handles pages or change markers, and reshapes the results into a common stream the rest of the platform can store, search, and remember.

The sub-stages group these adapters by the kind of tool they read from. Google Workspace covers mail, calendars, docs, drive files, meetings, and sheets. Collaboration connectors bring in Slack, Teams, Outlook, Notion, and Confluence knowledge. Work and engineering connectors read tasks, issues, code, incidents, and errors. Customer, marketing, finance, recruiting, scheduling, and form connectors do the same for their own business systems, from Salesforce and Stripe to Greenhouse and Airtable.

The YC source file adds a special intake path for YC material, including trusted guidance collections and controlled searches over YC directories such as companies, founders, jobs, and forums.

## Sub-stages

- [Google Workspace Source Connectors](stage-14.1.1.md) `stage-14.1.1` — 6 files
- [Collaboration, Messaging, and Knowledge Source Connectors](stage-14.1.2.md) `stage-14.1.2` — 5 files
- [Work Management and Engineering Operations Source Connectors](stage-14.1.3.md) `stage-14.1.3` — 9 files
- [CRM, Sales, and Customer Support Source Connectors](stage-14.1.4.md) `stage-14.1.4` — 6 files
- [Marketing, Ads, and Social Source Connectors](stage-14.1.5.md) `stage-14.1.5` — 6 files
- [Finance, Billing, Spend, and Commerce Source Connectors](stage-14.1.6.md) `stage-14.1.6` — 7 files
- [Recruiting, HR, and Workforce Source Connectors](stage-14.1.7.md) `stage-14.1.7` — 6 files
- [Horizontal Data, Scheduling, and Form Source Connectors](stage-14.1.8.md) `stage-14.1.8` — 3 files

## Files in this stage

### Concrete Source Connectors
### `extensions/yc/ufo_ext_yc/source.py`

`io_transport` · `source sync and tool request handling`

This file is the bridge between UFO and YC’s searchable data. Without it, an agent could not bring YC manuals, startup library material, or targeted Bookface-style directory search results into shared workspace memory for later use.

The file defines the allowed YC collections, the rules for configuring them, and the code that turns YC search output into internal `Page` objects. A `Page` is one saved piece of source material that the rest of the system can index and read later. Guidance collections are treated differently from directory searches: guidance is fetched without a user query, while directory collections require a query and a maximum result count.

The main worker is `YcSource`. During a sync, it checks whether the previous sync is still fresh, asks the YC command runner for search results, validates the returned JSON envelope, reads the CSV rows inside it, and converts each row into a page. It also caps each page’s size so one unusually large record cannot overload the source system.

The file also exposes `yc_index`, a tool entry point an agent can call to request a YC directory search be synced into shared memory. In plain terms, this is like asking a librarian to fetch a specific stack of YC records and put them on the shared desk.

#### Function details

##### `YcSourceConfig.validate_collection`  (lines 78–87)

```
def validate_collection(self) -> 'YcSourceConfig'
```

**Purpose**: This checks that a YC source configuration makes sense before the system tries to use it. It prevents impossible requests, such as asking a guidance library to run a search query, or asking a directory search with no query at all.

**Data flow**: It reads the chosen collection, optional query, and optional maximum result count from the config object. If the collection is a guidance collection, it rejects query-related settings. If the collection is a searchable directory, it requires a query and fills in a default result limit when none was supplied. The output is the same config object, either accepted and possibly completed with a default, or rejected with a clear error.

**Call relations**: This validation runs when `YcSourceConfig` is created, including inside `yc_index` when an agent asks to index YC directory results. It protects later code such as `YcSource.fetch` and `YcSource._fetch_collection` from receiving a configuration that the YC search tools cannot honor.


##### `YcSource.fetch`  (lines 137–154)

```
async def fetch(self, config: YcSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main sync method for a YC source. It decides whether a fresh fetch is needed, retrieves YC pages when necessary, and returns the result in the standard source-sync format used by the rest of the system.

**Data flow**: It receives a validated YC source config, an optional cursor from the previous sync, and source authentication information. It checks the current time against the cursor; if the previous sync is still recent enough, it returns no new pages and keeps the same cursor. Otherwise, it fetches the configured collection with a timeout, converts the successful fetch into a `SyncResult`, and writes a new cursor containing the sync time. If YC credentials are missing, it turns that into a skipped stream message rather than a hard crash.

**Call relations**: The source-sync framework calls this when it wants to refresh YC content. If work is needed, it hands off to `YcSource._fetch_collection` to do the actual YC query and page building. It then wraps those pages in `SyncResult` so the broader source pipeline can index them as a fresh snapshot.

*Call graph*: calls 2 internal fn (__init__, _fetch_collection); 5 external calls (__init__, __init__, timeout, now, timedelta).


##### `YcSource._fetch_collection`  (lines 156–212)

```
async def _fetch_collection(self, config: YcSourceConfig, auth: SourceAuth) -> tuple[Page, ...]
```

**Purpose**: This performs the actual paged YC search. It repeatedly asks the YC runner for results until it has fetched all available rows or reached the configured maximum.

**Data flow**: It receives a source config and authentication details. It builds a small JSON request for each page of results, calls the YC command runner, checks that the returned tool name and row counts are consistent, and converts the returned CSV into internal pages. It accumulates pages across result batches and returns them as a tuple.

**Call relations**: `YcSource.fetch` calls this when a sync is due. For every CSV response it receives from the YC runner, it calls `YcSource._pages` to choose the right parser for the collection type. Its validation steps make sure bad or mismatched YC responses are caught before they enter shared memory.

*Call graph*: calls 1 internal fn (_pages); called by 1 (fetch); 1 external calls (dumps).


##### `YcSource._pages`  (lines 214–217)

```
def _pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This chooses how to turn YC CSV output into pages based on the kind of collection being fetched. Guidance content and directory records have different CSV shapes, so they need different conversion paths.

**Data flow**: It receives the collection name and the CSV text returned by YC. If the collection is one of the guidance libraries, it sends the CSV to the guidance-page converter. Otherwise, it sends it to the directory-page converter. The output is a tuple of normalized `Page` objects.

**Call relations**: `YcSource._fetch_collection` calls this after each YC search response. It then delegates to either `YcSource._guidance_pages` or `YcSource._directory_pages`, keeping the higher-level fetch loop from needing to know the details of each CSV format.

*Call graph*: calls 2 internal fn (_directory_pages, _guidance_pages); called by 1 (_fetch_collection).


##### `YcSource._guidance_pages`  (lines 219–236)

```
def _guidance_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This converts YC guidance-library CSV rows into source pages. These pages represent authoritative YC material such as user manuals or startup library entries.

**Data flow**: It receives a collection name and CSV text. It reads each CSV row, validates the expected fields, combines the link, description, body, and categories into readable page text, trims the text if it is too large, and creates a `Page` with a stable source reference, stream name, title, and body. The output is all created pages as a tuple.

**Call relations**: `YcSource._pages` calls this for guidance collections. It uses `YcSource._bounded` before creating each page so the source system receives content that stays within the file’s size limit.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 3 external calls (__init__, DictReader, StringIO).


##### `YcSource._directory_pages`  (lines 238–268)

```
def _directory_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This converts YC directory-style CSV rows into source pages. Directory rows are flexible records, so this function preserves the useful fields as labeled text.

**Data flow**: It receives a collection name and CSV text. For each row, it pulls out the required record id and link, gathers every other non-empty field as a name-value attribute, formats those attributes into readable text, bounds the size, and creates a `Page`. The output is a tuple of pages ready for indexing.

**Call relations**: `YcSource._pages` calls this for searchable collections such as companies, founders, jobs, forum posts, and similar directory data. Like the guidance converter, it calls `YcSource._bounded` to keep each page within the allowed size before handing it back to the fetch loop.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 3 external calls (__init__, DictReader, StringIO).


##### `YcSource._bounded`  (lines 270–275)

```
def _bounded(self, body: str) -> str
```

**Purpose**: This keeps a page body from becoming too large. It protects the source system from one oversized YC record by cutting the text at a fixed byte limit and adding a clear truncation note.

**Data flow**: It receives a text body. It encodes the text as bytes to measure its real storage size. If it is already small enough, it returns it unchanged. If it is too large, it keeps only the allowed prefix, safely decodes it back to text, appends a truncation message, and returns the shortened body.

**Call relations**: Both `YcSource._guidance_pages` and `YcSource._directory_pages` call this just before creating `Page` objects. That means all YC pages, no matter which collection they came from, obey the same size bound.

*Call graph*: called by 2 (_directory_pages, _guidance_pages).


##### `yc_index`  (lines 278–302)

```
async def yc_index(ctx: ToolContext, args: YcIndexInput) -> ToolResult
```

**Purpose**: This is the tool an agent uses to ask for YC directory search results to be synced into shared memory. It does not return the search results directly; it registers a source sync request so the results can be fetched and indexed.

**Data flow**: It receives the current tool context and an input object containing the YC entity type, query, maximum result count, and a plain-language description. It checks that the YC extension context exists, verifies that YC credentials are available, registers a YC source using those search settings, and returns a short text confirmation telling the user what will be synced.

**Call relations**: This function is called when the YC indexing tool is dispatched during an agent turn. It creates a `YcSourceConfig`, then asks the extension context to register the source under the shared subject so later source-sync work can call `YcSource.fetch`. Its response tells the caller that repeating the request is safe because the registration is idempotent, meaning it should not create harmful duplicates.

*Call graph*: 3 external calls (__init__, __init__, __init__).

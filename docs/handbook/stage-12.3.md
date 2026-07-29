# Source connector registry and YC feeds  `stage-12.3`

This stage is shared behind-the-scenes support for bringing outside knowledge into the system. It helps the rest of the code find the right “connector,” meaning the small adapter that knows how to talk to a particular information source.

The registry file is the central address book. It lists the supported source connectors and gives the system one consistent way to name a connection. That name is built from the provider, account, and base web address, so the same source gets the same stable label each time. This keeps configuration predictable and avoids guessing which connector should be used.

The YC source file is a special connector for Y Combinator material that is not just a normal software-as-a-service account. It can load fixed guidance collections, such as manuals and startup library content, into workspace memory. It can also run limited searches over directory-style YC data, such as company and founder information. Together, the registry says how to find and name the connector, while the YC connector does the actual fetching.

## Files in this stage

### Connector Registry and YC Sources
Defines the shared connector registry and the specialized YC source ingestion paths for guidance, company, and founder feeds.

### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup and source registration`

This file answers two practical questions for the source-sync system: “Which external services do we know how to connect to?” and “What should we call a specific connected account?” Instead of searching the codebase automatically, it imports each connector class directly and lists it in one place. That makes startup predictable: adding a new provider means adding its connector to this registry, like writing a new contact into an address book.

The registry is built from connector classes such as Slack, GitHub, Zendesk, and many others. Each connector has a `name`, and that name becomes the lookup key used elsewhere when a source row says which backend it belongs to. The helper `_connector_registry` also checks for duplicate names, because two providers with the same key would make it unclear which connector should run.

The file also defines `binding_name`, the naming rule for one registered connection. It combines the provider, account, and optional base URL, turns that data into a short secure hash, and appends it to the provider name. This gives stable names that are short, readable, and unlikely to collide, even when the same provider is connected more than once.

#### Function details

##### `_connector_registry`  (lines 64–72)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the lookup table that maps each connector’s public name to the connector class that knows how to talk to that service. It protects the system from ambiguous setup by refusing to accept two connectors with the same name.

**Data flow**: It receives a tuple of connector classes. It reads each class’s `name`, adds that name and class to a dictionary, and stops with an error if the name has already been used. The result is a clean dictionary where a backend name can be used to find the correct connector class.

**Call relations**: When this module is loaded, the file calls this function to create `CONNECTORS` from the explicit list of imported provider connectors. Other parts of the source system can then rely on `CONNECTORS` as the single map from a backend name to the code that syncs that backend.


##### `binding_name`  (lines 79–85)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates the official stable name for a registered source binding. It is used so the same provider/account/base URL combination always gets the same compact name, while different combinations are very likely to get different names.

**Data flow**: It receives a provider name, an account identifier, and an optional base URL. It packages those values into sorted JSON text, hashes that text with SHA-256, keeps the first few hexadecimal characters, replaces underscores in the provider name with dashes, and returns a name like `provider-1a2b3c4d`. It does not change any stored data; it only returns the computed name.

**Call relations**: This function is the shared naming rule described by the registry: source objects derive their names from it, and page objects can link back through that same name. Inside the function, JSON serialization gives a consistent text form before hashing, and the hash supplies the short unique suffix.

*Call graph*: 2 external calls (sha256, dumps).


### `extensions/yc/ufo_ext_yc/source.py`

`io_transport` · `tool request and background source sync`

This file is the bridge between UFO’s shared memory system and YC’s search tools. Without it, a workspace could not ask to index YC guidance or save a focused YC directory search for later use by the assistant.

The file defines the rules for what can be requested. Guidance collections are treated like authoritative reference material, so they do not accept a search query. Directory collections are search-based, so they must include a query and a maximum number of results. This prevents broad, accidental imports of huge YC datasets.

When a sync runs, YcSource checks whether the data was refreshed recently enough. If so, it skips work and keeps the old cursor, which is like a bookmark saying, “we already checked this recently.” If the sync is needed, it calls the YC command runner, asks for results page by page, checks that the returned data matches what was requested, and converts each CSV row into a Page object that the workspace source system can store.

The file also includes yc_index, a tool-facing function. It is what a user action calls when they want to start syncing a YC directory search into shared memory. It verifies that YC credentials exist, registers the source, and returns a plain confirmation message.

#### Function details

##### `YcSourceConfig.validate_collection`  (lines 78–87)

```
def validate_collection(self) -> 'YcSourceConfig'
```

**Purpose**: This checks that a YC source request makes sense before the system tries to run it. It keeps guidance collections and searchable directory collections from being used with the wrong kind of options.

**Data flow**: It reads the chosen collection, query, and maximum result count from the config object. If the collection is guidance material, it rejects any query or result limit. If the collection is a directory search, it requires a query and fills in a default result limit when none was given. The same config object comes out, either accepted or with a clear validation error.

**Call relations**: This validation runs when a YcSourceConfig is created, including from yc_index when a user asks to index YC search results. It protects the later fetching code from impossible or unsafe requests.


##### `YcSource.fetch`  (lines 137–154)

```
async def fetch(self, config: YcSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main sync entry for a YC source. It decides whether the source needs refreshing, fetches new pages when needed, and returns the result in the format the workspace source system expects.

**Data flow**: It receives a source config, an optional cursor from the previous sync, and authentication context. It compares the cursor time with the configured refresh interval. If the data is still fresh, it returns no pages and keeps the old cursor. Otherwise it fetches the collection with a timeout, turns the current time into a new cursor, and returns the fetched pages as a fresh snapshot. If YC credentials are missing, it reports that the stream should be skipped instead of crashing the whole sync.

**Call relations**: The source sync system calls this when it wants YC data. When a refresh is needed, it calls YcSource._fetch_collection to do the real YC search work. It wraps the result into SyncResult so the wider source pipeline can store or skip the pages correctly.

*Call graph*: calls 2 internal fn (__init__, _fetch_collection); 5 external calls (__init__, __init__, timeout, now, timedelta).


##### `YcSource._fetch_collection`  (lines 156–212)

```
async def _fetch_collection(self, config: YcSourceConfig, auth: SourceAuth) -> tuple[Page, ...]
```

**Purpose**: This asks YC for one collection, page by page, until it has all allowed results. It is careful to verify that YC returned the tool and row counts it expected.

**Data flow**: It takes a validated config and source authentication data. It builds a small JSON request for each YC search page, including either guidance filters or a user query. It sends that request through the YC runner, parses the returned JSON envelope, checks the tool name and result counts, converts CSV results into Page objects, and stops when all results or the requested maximum have been collected. It returns a tuple of pages ready for storage.

**Call relations**: YcSource.fetch calls this only when a sync is due. For each batch of CSV results, it calls YcSource._pages, which chooses the right row-to-page conversion path for guidance or directory data.

*Call graph*: calls 1 internal fn (_pages); called by 1 (fetch); 1 external calls (dumps).


##### `YcSource._pages`  (lines 214–217)

```
def _pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This is a small router that chooses how to turn YC CSV text into workspace pages. Guidance rows and directory rows have different shapes, so they need different conversion rules.

**Data flow**: It receives the collection name and the CSV body returned by YC. It checks whether the collection is one of the guidance collections. If so, it sends the data to the guidance page converter; otherwise it sends it to the directory page converter. It returns the pages produced by that converter.

**Call relations**: YcSource._fetch_collection calls this after each YC search response. It hands work off to either YcSource._guidance_pages or YcSource._directory_pages so the rest of the fetching code does not need to know the exact CSV layout.

*Call graph*: calls 2 internal fn (_directory_pages, _guidance_pages); called by 1 (_fetch_collection).


##### `YcSource._guidance_pages`  (lines 219–236)

```
def _guidance_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This turns YC guidance CSV rows into Page objects that can be saved in shared memory. These pages represent reference material such as user manuals or startup library content.

**Data flow**: It receives a guidance collection name and CSV text. It reads each CSV row, validates the expected fields, joins the link, description, body, and categories into readable page text, trims the text if it is too large, and creates a Page with a stable source reference, stream name, title, and body. It returns all created pages as a tuple.

**Call relations**: YcSource._pages calls this when the collection is guidance material. For each row, it calls YcSource._bounded so oversized YC content does not exceed the source size limit before being handed to the storage pipeline.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 3 external calls (__init__, DictReader, StringIO).


##### `YcSource._directory_pages`  (lines 238–268)

```
def _directory_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This turns YC directory search CSV rows into Page objects. It is used for searchable collections such as companies, founders, investors, jobs, and similar YC directories.

**Data flow**: It receives a directory collection name and CSV text. It reads each row, separates the stable id and link from the other populated fields, formats the remaining fields as simple name-and-value lines, trims the result if needed, and creates a Page whose title is the link and whose body contains the link plus the row details. It returns the completed pages as a tuple.

**Call relations**: YcSource._pages calls this for non-guidance collections after YcSource._fetch_collection receives CSV data from YC. It also uses YcSource._bounded to keep each page within the configured byte limit.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 3 external calls (__init__, DictReader, StringIO).


##### `YcSource._bounded`  (lines 270–275)

```
def _bounded(self, body: str) -> str
```

**Purpose**: This keeps a page body from becoming too large for the source system. It cuts oversized text safely and adds a clear note that the content was truncated.

**Data flow**: It receives a text body and measures its encoded byte size. If it fits within the limit, it returns the body unchanged. If it is too large, it keeps only the allowed prefix, decodes it safely as UTF-8 text, appends a truncation message, and returns the shortened body.

**Call relations**: Both YcSource._guidance_pages and YcSource._directory_pages call this just before creating Page objects. It acts like a size gate between raw YC results and the workspace storage layer.

*Call graph*: called by 2 (_directory_pages, _guidance_pages).


##### `yc_index`  (lines 278–301)

```
async def yc_index(ctx: ToolContext, args: YcIndexInput) -> ToolResult
```

**Purpose**: This is the tool action that starts indexing a YC directory search into shared memory. It lets a user say, in effect, “search this YC collection and make up to this many results available to the workspace.”

**Data flow**: It receives a tool context and user-provided indexing arguments. It checks that the YC extension context exists, confirms YC credentials are available, builds a YcSourceConfig from the requested entity, query, and result limit, and registers that source for the shared workspace subject. It returns a ToolResult containing a human-readable confirmation message.

**Call relations**: This function is called from the tool layer when a user requests YC indexing. It creates the configuration that later drives YcSource.fetch during background syncing, so the immediate tool request schedules the work and the source sync pipeline performs the actual data collection.

*Call graph*: 3 external calls (__init__, __init__, __init__).

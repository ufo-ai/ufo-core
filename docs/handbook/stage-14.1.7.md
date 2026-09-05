# Customer support connectors  `stage-14.1.7`

This stage is part of the main syncing work. Its job is to visit customer-support tools and turn their data into source pages the system can store, search, and recall later. Think of it as a set of translators: each one speaks to a different helpdesk service, but all return records in a shape the rest of the system understands.

The Freshdesk connector knows how to log in to Freshdesk, which kinds of objects are available, and how to move through Freshdesk’s paged API results, meaning batches of data returned a page at a time. The Intercom connector does the same for conversations, contacts, companies, teams, tags, and tickets, smoothing out Intercom’s varied response formats into steady streams of records. The Zendesk connector covers Zendesk Support and related areas, including tickets, users, Help Center articles, and community posts.

Together, these files hide the differences between support platforms so the larger sync system can treat them as reliable sources of recallable information.

## Files in this stage

### Helpdesk connectors
Connectors for major customer-support platforms expose tickets, users, conversations, articles, and related support content as syncable source records.

### `extensions/sources/ufo_ext_sources/providers/freshdesk.py`

`io_transport` · `source sync`

Freshdesk exposes many kinds of data: tickets, conversations, contacts, companies, agents, settings, knowledge-base articles, forum discussions, and more. This file turns those Freshdesk API endpoints into named streams that the rest of the system can ask for in a consistent way. Without it, the system would not know where Freshdesk data lives, how to log in, or how to keep fetching more pages until a stream is complete.

The central piece is FreshdeskConnector. It builds an HTTP client using a Freshdesk API key, then serves records stream by stream. Some Freshdesk endpoints are simple: ask one URL, then follow the “next page” link in the response headers. Others need special paths. Tickets use numbered pages and can be filtered by an “updated since” cursor so later syncs only read newer changes. Conversations are nested under tickets, so the connector first reads tickets and then asks for each ticket’s conversations. Knowledge-base and forum data form trees, like folders inside cabinets: the connector walks parent categories, then child folders or forums, then final articles, topics, or comments.

One important behavior is refusal handling. If Freshdesk replies with 401 or 403, meaning the key is invalid or lacks permission, the stream is skipped with a clear reason instead of failing mysteriously. This connector is read-only; it does not create or update Freshdesk records.

#### Function details

##### `_stream`  (lines 56–70)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Freshdesk stream, such as tickets or contacts. The rest of the connector uses these descriptions to know the stream name, source object name, main identifier field, and optional update cursor.

**Data flow**: It receives a stream name and optional details like the Freshdesk source object, primary key, cursor field, and whether it is a main stream. It fills in sensible defaults, then returns a StreamSpec object that represents that stream in the wider source system.

**Call relations**: This helper is used while the file defines the Freshdesk stream list. It hands each completed StreamSpec to the connector class through FRESHDESK_STREAMS, so later sync code can discover what Freshdesk data is available.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector.record_identity`  (lines 110–113)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses the stable identity for a Freshdesk record. It has a special rule for the helpdesk settings stream because that endpoint returns one settings document rather than many normal records with separate IDs.

**Data flow**: It receives a record and the stream it came from. For the settings stream, it always returns the fixed identity "helpdesk"; for all other streams, it lets the shared REST connector choose the usual identity from the record.

**Call relations**: The broader sync process calls this when it needs to name or de-duplicate records. This method only steps in for settings; otherwise it relies on the parent connector’s normal record identity behavior.


##### `FreshdeskConnector.record_ref`  (lines 115–119)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses a human-friendly reference for a Freshdesk record when possible. For settings, it uses the primary language as a useful label instead of a numeric ID.

**Data flow**: It receives a record and its stream. If the stream is not settings, it delegates to the parent connector; if it is settings, it reads the record’s primary_language field and returns it as text when it is a string or number.

**Call relations**: The sync system uses this after records are fetched to make them easier to refer to. Like record_identity, this method only customizes the unusual settings stream and leaves ordinary streams to the shared REST behavior.


##### `FreshdeskConnector._make_client`  (lines 121–136)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client that talks to Freshdesk. It applies the right base URL, timeouts, JSON headers, and authentication so later requests can focus on fetching data.

**Data flow**: It receives a Freshdesk base URL and a resolved credential. It trims the URL, prepares request headers, and either uses a provided proxying transport or creates Basic Authentication using the API key as the username and "X" as the password. It returns an asynchronous HTTP client ready to call Freshdesk, or raises an error if no usable credential is present.

**Call relations**: The parent REST connector calls this during setup for a sync run. After it returns the client, pagination methods such as paginate and the lower-level page walkers use that client to make the actual API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 139–149)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Builds the query options for one page of Freshdesk tickets. It keeps ticket fetching consistent by always asking for the same page size, sort order, and included extra details.

**Data flow**: It receives an optional cursor and a page number. It creates a dictionary of request parameters: 100 tickets per page, the requested page, sorted by updated time, with description, requester, and stats included. If a cursor is present, it adds updated_since so Freshdesk only returns tickets changed after that point.

**Call relations**: _paginate_tickets calls this before each ticket request. It hands back the exact parameters that are sent to the Freshdesk tickets endpoint.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 151–178)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading one Freshdesk stream page by page. Given a stream name and optional cursor, it decides which Freshdesk paging pattern to use and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It first asks whether the stream needs special treatment, such as tickets or nested forum data. If not, it looks up the simple Freshdesk path, handles the one-document settings endpoint separately, or follows link-header pagination for normal list endpoints. It yields lists of records as they arrive. If Freshdesk refuses access with 401 or 403, it turns that into a clear skipped-stream result.

**Call relations**: The wider source runtime calls paginate when it wants records for a stream. paginate then either delegates to _special_pages, uses _paginate_link_header for ordinary endpoints, or directly reads settings. It is the traffic director for all Freshdesk reading.

*Call graph*: calls 3 internal fn (__init__, _paginate_link_header, _special_pages).


##### `FreshdeskConnector._special_pages`  (lines 180–221)

```
def _special_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]] | None
```

**Purpose**: Chooses the custom paging routine for streams that do not fit the simple one-URL pattern. This covers tickets, conversations, canned responses, solutions, and discussion/forum trees.

**Data flow**: It receives the HTTP client, a stream name, and an optional cursor. It checks the stream name against known special cases and returns the matching asynchronous page generator. If the stream is ordinary, it returns nothing so paginate can use the standard path lookup instead.

**Call relations**: paginate calls this before trying simple endpoint pagination. When a match is found, _special_pages hands control to _paginate_tickets, _paginate_conversations, _paginate_two_level, or _paginate_three_level, depending on the shape of the Freshdesk data.

*Call graph*: calls 4 internal fn (_paginate_conversations, _paginate_three_level, _paginate_tickets, _paginate_two_level); called by 1 (paginate).


##### `FreshdeskConnector._paginate_link_header`  (lines 223–230)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk endpoints that advertise the next page through an HTTP Link header. A Link header is a response header that points to the next URL, like a “next” button on a web page.

**Data flow**: It receives an HTTP client, an endpoint path, and optional query parameters. It asks the shared REST connector to fetch pages with a page size of 100 and then yields each page of records it gets back.

**Call relations**: paginate uses this for most simple Freshdesk streams. The nested walkers also use it whenever they need to list children under a parent object, so this function is the common page-by-page reader for many Freshdesk endpoints.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 232–249)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk tickets using Freshdesk’s numbered-page system. It supports incremental sync through the updated_since cursor and stops before Freshdesk’s known 300-page limit.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket query parameters, requests the ticket endpoint, extracts the list of ticket records, and yields each non-empty page. It stops when there are no records, when a page has fewer than 100 records, or when the next page would pass the 300-page ceiling.

**Call relations**: _special_pages uses this when the requested stream is tickets. _paginate_conversations also depends on it, because conversations are found by first walking the tickets that match the cursor.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, _special_pages).


##### `FreshdeskConnector._paginate_conversations`  (lines 251–267)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads ticket conversations by first finding tickets, then asking Freshdesk for the conversations under each ticket. This is needed because conversations are not fetched from one flat global list in this connector.

**Data flow**: It receives an HTTP client and an optional cursor. It reads ticket pages through _paginate_tickets, takes each ticket ID, fetches that ticket’s conversations with link-header pagination, and adds the ticket_id to each conversation record if it is missing. It yields conversation pages as they are found.

**Call relations**: _special_pages calls this for the conversations stream. It combines _paginate_tickets and _paginate_link_header: tickets provide the parent IDs, and link-header pagination retrieves the child conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_two_level`  (lines 269–280)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks Freshdesk data arranged as parents with direct children, such as categories with forums or folders with canned responses. It is a reusable way to say: list the parents, then list each parent’s children.

**Data flow**: It receives an HTTP client, a parent endpoint path, and a child endpoint template containing a parent ID placeholder. It fetches parent pages, reads each parent’s ID, fills that ID into the child path, and yields the child pages. Parents without IDs are skipped.

**Call relations**: _special_pages returns this walker for several two-step Freshdesk structures. Internally it uses _paginate_link_header for both parent lists and child lists, so it reuses the standard Freshdesk page-following behavior.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_three_level`  (lines 282–305)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks Freshdesk data arranged in three layers, specifically structures like solution categories, folders, and articles. It is used when the connector must pass through two levels of containers before reaching the records to sync.

**Data flow**: It receives an HTTP client plus paths for the root level, middle level, and leaf level. It fetches root records, uses each root ID to fetch middle records, uses each middle ID to fetch final leaf records, and yields the leaf pages. Any category or folder without an ID is skipped because there is no safe child URL to build.

**Call relations**: _special_pages calls this for solution articles. Like the two-level walker, it relies on _paginate_link_header at each layer, but adds one extra step before yielding the final records.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


### `extensions/sources/ufo_ext_sources/providers/intercom.py`

`io_transport` · `source sync / API pagination`

Intercom exposes useful customer-support data, but it does not offer all of it in one simple list. Some data is searched with a POST request, some is read through a scrolling cursor, some comes back as one plain list, and some must be fetched by first reading a parent item and then asking for its children. This file is the adapter that hides those differences from the rest of the project.

It defines the Intercom streams the system knows about, including conversations, contacts, companies, conversation parts, company segments, and activity logs. A stream is a named kind of data with details like its main ID field and, when possible, the time field used for incremental syncing. Incremental syncing means “only ask for records newer than the last one we already saw.”

The `IntercomConnector` builds an authenticated HTTP client, adds the Intercom API version header, chooses the right paging method for each stream, and yields records in batches. It also flattens a few nested Intercom fields into simpler top-level fields, like copying a conversation source subject to `source__subject`. This matters because later database or transform steps can more easily read flat fields than deeply nested JSON.

If Intercom refuses access because the token lacks permission, this connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Intercom data. This keeps the stream list compact and consistent instead of repeating the same setup for every Intercom object.

**Data flow**: It receives a stream name plus optional details such as the Intercom object name, the primary key, the cursor field, and whether it is a main stream. It fills in sensible defaults, then returns a `StreamSpec`, which is the system's small description card for that stream.

**Call relations**: This helper is used while the file is being loaded to build `INTERCOM_STREAMS`. It hands each finished stream description to the connector class so the wider source system knows what Intercom data can be read.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector.record_identity`  (lines 101–105)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses the stable identity for a record when the normal rule is not good enough. It has special behavior for Intercom data-attribute streams, where an attribute may be best identified by `id` or by `full_name`.

**Data flow**: It receives one record and the stream it belongs to. For ordinary streams, it uses the parent connector's identity rule. For attribute streams, it looks for `id` first, then `full_name`, and returns that value as text if it is usable; otherwise it returns nothing.

**Call relations**: The sync framework asks this when it needs to name or deduplicate records. This method either answers directly for Intercom attributes or passes the decision back to the shared REST connector behavior.


##### `IntercomConnector.record_ref`  (lines 107–111)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses a human-friendly reference label for records that are better recognized by name. For tags, teams, and data attributes, the name is often more helpful than an internal ID.

**Data flow**: It receives a record and its stream. If the stream is not one of the special named streams, it delegates to the parent connector. If it is special, it reads the record's `name` field and returns it as text when possible.

**Call relations**: This supports the broader sync system when it wants a readable label for a stored record. It complements `record_identity`: identity is for stable uniqueness, while this reference is for easier recognition.


##### `IntercomConnector._make_client`  (lines 113–116)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header. Without this header, Intercom may use a different API version or reject behavior the connector expects.

**Data flow**: It receives the base URL and credential. It asks the parent REST connector to create an authenticated async HTTP client, adds `Intercom-Version: 2.11` to its headers, and returns the prepared client.

**Call relations**: The base source machinery calls this when setting up the connector. After this point, all pagination methods use the returned client for their Intercom requests.


##### `IntercomConnector._build_search_body`  (lines 119–149)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for Intercom's search endpoints. It combines page size, sort order, page cursor, and the “only newer than this” filter into the shape Intercom expects.

**Data flow**: It receives a stream, the saved incremental cursor, and Intercom's `starting_after` page marker. It creates a JSON body that asks for up to 150 records, sorted oldest-to-newest by the stream cursor field, and filters out records at or before the saved cursor. It returns that body for a POST request.

**Call relations**: `_paginate_search` uses this for normal searchable streams such as conversations, contacts, and tickets. `_paginate_conversation_parts` also uses it to first find conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 152–157)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first object from a list when Intercom wraps related records inside nested arrays. It avoids assuming that the value is really a non-empty list of dictionaries.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary-like record, it returns that first item. Otherwise it returns nothing.

**Call relations**: The flattening helpers use this when they want the first related contact or company from Intercom's nested response structure. It is a small safety check before copying nested fields.


##### `IntercomConnector._flatten_conversation`  (lines 160–177)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes important nested conversation fields easier to read later. It copies selected fields from `source` and the first related contact onto the top level of the conversation record.

**Data flow**: It receives one conversation record. It makes a shallow copy, reads nested `source` fields such as type, subject, and body, and writes them as flat keys like `source__subject`. It also looks for the first contact and writes its ID as `requester_id`. The original record content remains otherwise intact.

**Call relations**: `flatten` calls this only for the conversations stream. The result is then passed back to the sync pipeline in a shape that downstream SQL or transformation code can read more simply.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 180–189)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes the author of a conversation part easy to query. A conversation part is an individual message or event inside a conversation.

**Data flow**: It receives one conversation-part record. It copies the record, reads the nested `author` object when present, and adds `author_type` and `author_id` as top-level fields. It leaves the `conversation_id` field alone if it was already stamped during pagination.

**Call relations**: `flatten` calls this for records from the `conversation_parts` stream. Those records usually come from `_paginate_conversation_parts`, which attaches the parent conversation ID before flattening happens.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 192–200)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes a contact's first associated company easier to find. It lifts that company ID into a simple `org_id` field.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested `companies` envelope, takes the first company record if present, and writes either its `id` or `company_id` to `org_id`. It returns the enriched copy.

**Call relations**: `flatten` calls this for the contacts stream. The added `org_id` gives later processing a direct link from a contact to an organization without digging through nested Intercom JSON.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 202–216)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes Intercom records before the rest of the system stores or transforms them. It flattens selected nested fields and converts numeric cursor values into strings for the system's watermark tracking.

**Data flow**: It receives a raw record and its stream description. Depending on the stream name, it sends the record through the matching flattening helper. Then, if the stream has a cursor field and that field is an integer, it returns a copy where the cursor is written as decimal text. Otherwise it returns the record as-is.

**Call relations**: The source pipeline calls this after records are fetched. It hands off to `_flatten_conversation`, `_flatten_conversation_part`, or `_flatten_contact` for stream-specific cleanup, then returns the normalized record to the shared sync machinery.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 218–234)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the main stream-reading interface for Intercom. It yields pages of records and turns permission failures into a controlled “stream skipped” result.

**Data flow**: It receives an HTTP client, a stream description, and the last saved cursor. It asks `_stream_pages` for the right page iterator and yields each page it receives. If Intercom responds with HTTP 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` with a clear message; other HTTP errors continue upward.

**Call relations**: The broader sync runner calls this when it wants records for one Intercom stream. This method is the protective wrapper around the stream-specific pagination methods selected by `_stream_pages`.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `IntercomConnector._stream_pages`  (lines 236–254)

```
def _stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct paging strategy for each Intercom stream. It is the traffic director that knows which streams use search, scroll, plain lists, child lookups, or activity-log paging.

**Data flow**: It receives the HTTP client, stream description, and saved cursor. It checks the stream name and returns the matching async page iterator. If no strategy is known for that stream, it raises an error instead of silently doing the wrong thing.

**Call relations**: `paginate` calls this at the start of stream reading. It then hands control to helpers such as `_paginate_search`, `_paginate_scroll`, `_paginate_list`, `_paginate_attributes`, `_paginate_conversation_parts`, `_paginate_company_segments`, or `_paginate_activity_logs`.

*Call graph*: calls 7 internal fn (_paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search); called by 1 (paginate).


##### `IntercomConnector._paginate_search`  (lines 256–276)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that use Intercom's search API, such as conversations, contacts, and tickets. It keeps asking for the next search page until Intercom says there are no more.

**Data flow**: It receives the HTTP client, stream, and saved cursor. For each loop, it builds a search request body, posts it to the stream's search endpoint, extracts the records from the right response key, and yields them if any exist. It then reads Intercom's `starting_after` marker and uses it to request the next page.

**Call relations**: `_stream_pages` sends searchable streams here. This method depends on `_build_search_body` to format each POST request correctly before handing pages back to `paginate`.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_scroll`  (lines 278–292)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies through Intercom's scroll API. A scroll API works like asking for the next tray of cards using a token returned with the previous tray.

**Data flow**: It starts without a scroll token, asks `/companies/scroll` for data, yields the returned company records, then saves the returned `scroll_param` token for the next request. It stops when there are no records or no next token.

**Call relations**: `_stream_pages` uses this for the companies stream. It feeds pages of companies back through the same connector pipeline as all other stream readers.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_list`  (lines 294–306)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom streams that come back as a simple one-shot list, such as admins, tags, teams, or segments. These do not need cursor-based paging in this connector.

**Data flow**: It receives the client and stream, requests the stream's list endpoint, and looks for records under either the stream name or the generic `data` key. If it finds a non-empty list, it yields that list once and then finishes.

**Call relations**: `_stream_pages` sends plain list streams here. This is the simplest pagination path and returns its single page to `paginate` without looping through cursors.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_attributes`  (lines 308–317)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data attributes for companies or contacts. Data attributes describe custom fields that can exist on those objects.

**Data flow**: It receives the client and stream, translates the stream name into the Intercom model name, and requests `/data_attributes` with that model as a parameter. It filters the response down to dictionary-like records and yields them if any are present.

**Call relations**: `_stream_pages` calls this for company and contact attribute streams. The records it yields later use the connector's special identity and reference rules because attributes do not behave exactly like ordinary objects.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_conversation_parts`  (lines 319–351)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the messages or parts inside conversations. Intercom does not provide this as one direct top-level list, so the connector first finds conversations and then fetches each conversation's details.

**Data flow**: It receives the client and saved cursor. It searches conversations page by page, then for each conversation with an ID it requests `/conversations/{id}`. From that detail response it extracts `conversation_parts`, stamps each part with the parent `conversation_id`, and yields the parts in batches. It follows the conversation search page marker until there are no more conversation pages.

**Call relations**: `_stream_pages` uses this for the `conversation_parts` stream. It uses `_build_search_body` to page through parent conversations, then hands child records back so `flatten` can add author fields later.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_company_segments`  (lines 353–377)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. A segment is a grouping label in Intercom, and the connector must look them up company by company.

**Data flow**: It scrolls through companies using `/companies/scroll`. For each company with an ID, it requests `/companies/{id}/segments`, extracts the returned segment records, stamps each segment with `company_id`, and yields the segment batch. It continues until the company scroll has no next token.

**Call relations**: `_stream_pages` calls this for the `company_segments` stream. It combines the company scroll pattern with child lookups, then returns segment records that still carry the company they came from.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_activity_logs`  (lines 379–401)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity logs, optionally starting after a saved creation time. Activity logs use their own paging style with next links rather than the search or scroll patterns.

**Data flow**: It receives the client and optional cursor. If a cursor exists, it sends it as `created_at_after` on the first request to `/admins/activity_logs`. It yields any returned `activity_logs`, then follows the `pages.next` path when Intercom provides one. If the next link is a full URL, it strips the base URL so the client can request the relative path.

**Call relations**: `_stream_pages` sends the activity log stream here. This method returns each page to `paginate` while handling Intercom's next-link format internally.

*Call graph*: called by 1 (_stream_pages).


### `extensions/sources/ufo_ext_sources/providers/zendesk.py`

`io_transport` · `source sync pagination`

Zendesk has many different API endpoints, and they do not all page through results in the same way. This file is the adapter that hides those differences. It defines the list of Zendesk streams the system can read, then provides one connector class, `ZendeskConnector`, that knows how to fetch each stream page by page.

The main job is to ask Zendesk for batches of records, yield each batch, and keep following Zendesk’s “next page” links until there is nothing left. Some busy streams, such as tickets and users, use Zendesk’s incremental export API. That means the connector can start from a saved time cursor and only read changes since then, like resuming a long audiobook from a bookmark. Simpler streams use ordinary page links.

A few streams need special treatment. Ticket comments are not fetched from a normal comments endpoint; they are pulled out of ticket event data and reshaped into comment records. User identities are fetched by first reading users, then asking for each user’s identities. Tickets can also bring along related user data, so this file copies requester, submitter, and assignee email addresses directly onto ticket records.

If Zendesk refuses access with a 401 or 403 response, the connector reports that the stream should be skipped, rather than pretending the data is empty. The connector only reads data; it does not create or update Zendesk records.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one Zendesk data type. A stream description tells the sync system what the stream is called, where it comes from in Zendesk, which field uniquely identifies records, and which time fields can be used for change tracking.

**Data flow**: It receives a stream name plus optional details such as the Zendesk source object, primary key, cursor field, and timestamp fields. It fills in sensible defaults when details are not supplied, then returns a `StreamSpec`, which is the system’s small recipe for reading that stream.

**Call relations**: This helper is used while the file is being loaded to build `ZENDESK_STREAMS`, the full menu of Zendesk data the connector offers. It hands its settings into `StreamSpec.__init__`, so the rest of the connector can later use those stream recipes without repeating the same setup over and over.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This function enriches records using extra related data that Zendesk returned in the same API response. In practice, it lets ticket records get useful email fields from sideloaded user records.

**Data flow**: It receives the main records, the full API page, and instructions that say which ID fields should be matched to which sideloaded arrays. It builds a lookup table from the sideloaded data, finds matching related records, and writes email addresses onto the original records when those email fields are missing.

**Call relations**: It is called by `ZendeskConnector._paginate_incremental_cursor` when a stream, currently tickets, asks Zendesk to include related users. The paginator fetches the page, then this function fills in the extra human-friendly email fields before the records are yielded to the sync system.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This small helper decides which JSON field in a Zendesk response contains the records for a stream. Most streams use their own name, but some Zendesk endpoints use a different word.

**Data flow**: It receives a `StreamSpec`. It checks whether that stream name has a special response-field override, and returns either the override or the stream name itself.

**Call relations**: It is called by `ZendeskConnector._paginate_default` before reading ordinary paged endpoints. This lets the default paginator work for both straightforward responses and Zendesk endpoints whose list field has an unusual name.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This helper turns a saved cursor into the Unix timestamp format Zendesk’s incremental APIs expect. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be empty, already numeric, or an ISO date-time string. Empty or unparseable values become `0`, numeric strings become integers, and date-time strings are parsed and converted to seconds. If a date-time has no timezone, it is treated as UTC, meaning Coordinated Universal Time.

**Call relations**: The incremental paginators call this before building their first Zendesk request. It is used by `ZendeskConnector._paginate_incremental_cursor`, `ZendeskConnector._paginate_ticket_comments`, and `ZendeskConnector._paginate_user_identities` so each can resume from the correct point in time.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This helper converts Zendesk’s full next-page URL into just the path and query string the connector needs for its request method. It keeps pagination moving without depending on the full host name in Zendesk’s response.

**Data flow**: It receives a next-page URL or nothing. If there is no URL, or the URL has no path, it returns nothing. Otherwise, it parses the URL, keeps the path, appends the query string when present, and returns that shorter request path.

**Call relations**: All pagination methods call this after reading a page from Zendesk. `ZendeskConnector._paginate_default`, `ZendeskConnector._paginate_incremental_cursor`, `ZendeskConnector._paginate_ticket_comments`, and `ZendeskConnector._paginate_user_identities` use its result as the next path to request, or stop when it returns nothing.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Zendesk stream. Given a stream, it chooses the right pagination method for that kind of Zendesk data and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, sends ticket comments, user identities, incremental streams, or ordinary streams to the matching helper, and yields each list of records those helpers produce. If Zendesk rejects access with a 401 or 403 response, it raises `StreamSkipped` with a clear message instead of returning misleading data.

**Call relations**: The broader source runner calls this when it wants records from a Zendesk stream. This function then delegates to `ZendeskConnector._paginate_ticket_comments`, `ZendeskConnector._paginate_user_identities`, `ZendeskConnector._paginate_incremental_cursor`, or `ZendeskConnector._paginate_default`, depending on the stream. It also turns permission failures into a controlled skip signal for the runner.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads high-volume Zendesk streams using Zendesk’s incremental cursor API. It is meant for data that can be resumed from a time cursor, such as tickets, users, organizations, and ticket metric events.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It converts the cursor to a Unix timestamp, builds the first incremental export URL, fetches each page, extracts the records, optionally enriches them with sideloaded data, yields non-empty batches, and follows Zendesk’s next cursor URL until Zendesk says the stream has ended.

**Call relations**: `ZendeskConnector.paginate` calls this for streams listed as incremental cursor streams. Inside the loop it uses `ZendeskConnector._cursor_to_unix` to start at the right time, `_apply_sideload` to add related user emails for tickets, and `ZendeskConnector._next_page_path` to move from one Zendesk page to the next.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Zendesk streams that use normal page-by-page API responses. It is the general path for streams that do not need special incremental or fan-out behavior.

**Data flow**: It receives an HTTP client and a stream description. It builds the first `/api/v2/...` request with a fixed page size, fetches a page, pulls the records from the correct response field, yields the records when present, and follows the `next_page` link until there are no more pages.

**Call relations**: `ZendeskConnector.paginate` calls this when no special stream rule applies. It asks `ZendeskConnector._data_field` which JSON field contains the records, then uses `ZendeskConnector._next_page_path` after each response to continue paging.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function turns Zendesk ticket event data into a stream of ticket comments. Zendesk exposes comments inside ticket events here, so this code pulls those comment events out and makes them look like regular records.

**Data flow**: It receives an HTTP client and an optional cursor. It requests incremental ticket events from the cursor time, scans each event’s child events, keeps only child events whose type is `Comment`, copies each comment into a new record, adds the parent `ticket_id`, normalizes numeric creation times into readable UTC date-time strings, yields comment batches, and follows the next cursor link until the event stream ends.

**Call relations**: `ZendeskConnector.paginate` calls this specifically for the `ticket_comments` stream. It uses `ZendeskConnector._cursor_to_unix` to build the starting request, `datetime.fromtimestamp` when a comment time is numeric, and `ZendeskConnector._next_page_path` to continue through Zendesk’s event pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads identity records for users, such as alternate emails or login identities. Zendesk requires looking these up user by user, so the function first pages through users and then fetches each user’s identities.

**Data flow**: It receives an HTTP client and an optional cursor. It reads users incrementally from the cursor time, skips invalid user entries, builds an identities URL for each valid user ID, pages through that user’s identity records, yields any identities found, then moves to the next user page until Zendesk says the user stream has ended.

**Call relations**: `ZendeskConnector.paginate` calls this for the `users_identities` stream. It relies on `ZendeskConnector._cursor_to_unix` to start from the right user update time and `ZendeskConnector._next_page_path` both for identity pagination and for moving through the incremental user pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).

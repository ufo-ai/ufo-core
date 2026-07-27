# HR, payroll, and recruiting connectors  `stage-13.1.8`

This stage is a set of behind-the-scenes connectors for people and hiring tools. A connector is a translator: it knows how to talk to one outside service, ask for data, and reshape the answers into a common stream of records the rest of the system can sync, store, and search.

The recruiting connectors cover the hiring pipeline. Ashby reads candidates, jobs, applications, interviews, offers, and lookup lists from Ashby’s page-by-page API. Greenhouse does the same for Greenhouse Harvest, including sign-in, paging, and nested items such as interviews inside applications. Recruitee pulls candidates, job offers, departments, and other hiring records.

The HR and payroll connectors cover employee operations after hiring. BambooHR reads employee details, time off, timesheets, reports, and metadata, even though BambooHR returns these in several different shapes. Deel reads contracts, payslips, timesheets, tasks, and forms. Rippling reads companies, workers, and teams. Together, these files act like adapters for different plugs, making many systems feed one shared sync pipeline.

## Files in this stage

### Recruiting sources
Connectors that sync hiring pipelines, candidates, jobs, applications, interviews, offers, and recruiting lookup data.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `during source sync`

Ashby is a recruiting system, and its API exposes many kinds of hiring data. This file is the read-only connector for that API. Without it, the platform would not know which Ashby endpoints exist, how to authenticate, or how to walk through Ashby’s paged results.

The file first defines the Ashby streams: each stream is a named kind of data, such as candidates or job postings, paired with the exact Ashby endpoint used to fetch it. Most streams use an `updatedAt` field as a cursor, meaning a saved “last seen” timestamp can be sent back to Ashby so later syncs only ask for changed records.

The `AshbyConnector` then supplies the practical rules. Authentication uses HTTP Basic authentication, but with Ashby’s API key as the username and an empty password. The connector converts the key into the right header unless a broker-provided transport is already in charge.

Fetching data is mostly a loop: send a POST request with a page size, yield any returned records, then follow Ashby’s `nextCursor` until Ashby says there is no more data. One stream is different: criteria evaluations are not listed on their own. The connector first lists applications, then asks for evaluations for each application, like checking each folder in a filing cabinet one by one.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Ashby data type. It keeps the large stream list readable by filling in common details, such as the created and updated timestamp fields.

**Data flow**: It receives a friendly stream name, the Ashby API path, and optional details like the primary key and cursor field. It packages those choices into a `StreamSpec`, which is the connector’s standard description of how one kind of record should be read.

**Call relations**: The file uses this helper while building the Ashby stream catalog. Internally it hands the collected settings to `StreamSpec.__init__`, so the rest of the connector can treat every Ashby stream in the same structured way.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Ashby. Its main job is to turn an Ashby API key into the Basic authentication header Ashby expects.

**Data flow**: It receives a base URL and a credential. If the credential already carries a special transport, it leaves setup to the parent connector. Otherwise it reads the API key from the credential, encodes `key:` with Base64, and creates a new credential containing an `Authorization: Basic ...` header. If there is no API key, it stops with an error because Ashby cannot be called safely.

**Call relations**: The connector calls this when it needs a client for API requests. It uses `base64.b64encode` to make the Basic-auth token and `Credential.__init__` to wrap the finished header before handing client creation back to the shared REST connector machinery.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the routing point for reading pages from Ashby. It decides whether a stream can use the normal Ashby paging pattern or needs the special per-application lookup.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. For ordinary streams, it passes those on to the default paging loop. For `application_criteria_evaluations`, it ignores the normal stream path and uses the special application fanout process. It yields lists of records as they arrive.

**Call relations**: The wider REST connector asks this method for pages during a sync. This method then calls either `AshbyConnector._paginate_default` for most streams or `AshbyConnector._paginate_application_criteria` for the special criteria-evaluation stream, and passes each produced page back upward.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal Ashby list endpoint from start to finish. It follows Ashby’s cursor-based paging, where each response can point to the next page.

**Data flow**: It starts with a request body containing the page limit. If a saved sync cursor exists, it adds it as Ashby’s `syncToken` so Ashby can return only changed records. It repeatedly sends a POST request, yields the `results` records if there are any, then checks whether Ashby says more data is available. If so, it sends the next request with Ashby’s `nextCursor`; if not, it finishes.

**Call relations**: This is called by `AshbyConnector.paginate` for every ordinary stream. It is the connector’s standard page-turning loop, handing batches of records back to `paginate`, which then hands them to the broader sync process.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads criteria evaluations, which Ashby only exposes by application ID. It first finds applications, then asks Ashby for the evaluations belonging to each one.

**Data flow**: It pages through `/application.list` using the same cursor style as other list endpoints. For each application record, it reads the application ID. If the ID is present, it POSTs that ID to `/application.listCriteriaEvaluations`, copies each returned evaluation, and makes sure the application ID is included on the row. It yields batches of stamped evaluation records and continues until there are no more application pages.

**Call relations**: This is called only by `AshbyConnector.paginate` when the requested stream is `application_criteria_evaluations`. It exists because the ordinary paging helper cannot fetch this data directly; it must fan out from each application to the matching evaluation endpoint.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `during source sync and API pagination`

Greenhouse is a recruiting system, and its Harvest API exposes many kinds of hiring data: candidates, jobs, applications, interviews, offers, users, and supporting lookup lists. This file is the read-only connector that turns those API endpoints into named streams the rest of the system can sync.

At the top, the file declares all the streams it knows about. A stream is a named feed of records, like “candidates” or “jobs_stages”. Some streams are simple: the connector asks one Greenhouse URL and receives a flat list of records. Others are “per-parent” streams. For example, to get a candidate’s activity feed, the connector first lists candidates, then asks for the activity feed for each candidate. It adds the parent id to each child record so later code can still tell where that child came from.

The connector also knows Greenhouse’s paging style. Greenhouse returns a page of results and puts the next-page address in an HTTP Link header, like a “next” sign on a trail. The connector follows those signs until there are no more pages.

Authentication is Greenhouse-specific: the API key is sent as an HTTP Basic username with an empty password when the key is available locally. If a permission problem appears, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream description in one compact call. It gives the rest of the connector a clear recipe for one Greenhouse feed: its name, where it comes from, which field identifies records, and which date field can be used for incremental syncing.

**Data flow**: It receives stream settings such as the stream name, source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, such as using the stream name as the source object when none is provided. It returns a StreamSpec object, which is the system’s standard description of a syncable stream.

**Call relations**: The module uses this helper while loading to build the full list of Greenhouse streams. Internally it hands the finished settings to StreamSpec.__init__, so the rest of the connector can work with normal StreamSpec objects instead of loose dictionaries.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Greenhouse. Its main job is to adjust authentication so Greenhouse receives the API key in the Basic Auth format it expects.

**Data flow**: It receives a base URL and a resolved credential. First it asks the parent RestConnector to build the normal HTTP client. If the credential contains a bearer value, this method treats that value as the Greenhouse API key, installs HTTP Basic authentication with an empty password, and removes the usual Authorization header. It returns the configured async HTTP client.

**Call relations**: This method is the connector-specific client setup hook. It builds on the parent connector’s client creation, then uses httpx.BasicAuth to match Greenhouse’s authentication rules before any pagination or stream reading begins.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This chooses the Greenhouse query parameter used for incremental syncing. Most Greenhouse streams use updated_after, but a few endpoints use different names.

**Data flow**: It receives a stream name. It checks a small lookup table for special cases, such as applications and eeoc, and otherwise falls back to updated_after. It returns the parameter name that should be sent to Greenhouse.

**Call relations**: GreenhouseConnector.paginate calls this when it has a cursor value to send. This keeps the main pagination code simple while still respecting Greenhouse’s endpoint-by-endpoint naming differences.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading path for a Greenhouse stream. Given a stream and an optional saved cursor, it yields pages of records from the right Greenhouse endpoint.

**Data flow**: It receives an HTTP client, a stream description, and possibly a cursor value from a previous sync. If the stream is a per-parent stream, it delegates to the parent-child pagination path. Otherwise it finds the stream’s simple API path, adds the page size, and, when possible, adds the correct incremental filter parameter. It then yields each page of records. If Greenhouse replies with 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped so the stream is recorded as skipped rather than crashing the whole run.

**Call relations**: This is the dispatcher for stream reads. It calls _paginate_per_parent for nested streams, _cursor_param to name the incremental filter, and _paginate_link_header for ordinary Greenhouse list endpoints. When access is refused, it hands control to StreamSkipped so the larger sync flow can continue safely.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This follows Greenhouse’s standard page-by-page API pattern. It keeps asking for the next page while Greenhouse provides a Link header pointing to one.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the inherited link-header paging helper to fetch pages using the connector’s page size. Each returned page, a list of records, is yielded unchanged.

**Call relations**: GreenhouseConnector.paginate uses this for normal top-level streams. GreenhouseConnector._paginate_per_parent also uses it twice: first to list parents, then to list each parent’s children. This method is the small adapter between Greenhouse’s paging convention and the shared RestConnector paging helper.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads nested Greenhouse collections that only exist under another object, such as job openings under each job or permissions under each user. It preserves the parent-child relationship by adding the parent id to every child record.

**Data flow**: It receives an HTTP client, the parent collection path, a child path template, and the field name where the parent id should be stored. It pages through all parents, pulls each parent’s id, then pages through that parent’s child endpoint. For every child record that is a dictionary-like object, it adds the parent id if that field is not already present. It yields child pages outward.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is listed in the per-parent stream map. This method depends on _paginate_link_header for both parent and child API calls, so all Greenhouse paging still flows through the same Link-header logic.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `source sync data fetching`

Recruitee is an online recruiting tool, and its API returns data in pages, much like search results on a website. This file defines a Recruitee connector: a small adapter that knows which Recruitee lists exist, where to ask for them, and how to keep asking until there are no more full pages left.

The connector declares three streams of data: candidates, offers, and departments. A stream is just one kind of list the sync can read. Candidates and offers are marked as canonical, meaning they are treated as central record types for this source. Each stream uses an id field as its stable identifier.

The base URL is intentionally left blank because Recruitee URLs include a company-specific tenant ID. That tenant-specific address must be supplied by the sync setup. This avoids accidentally calling a wrong or generic host.

The main behavior is pagination. The connector asks Recruitee for page 1, then page 2, and so on, requesting up to 100 records each time. If Recruitee refuses access with a 401 or 403 status, the connector does not crash the whole sync as an unknown failure. Instead, it raises a clear “stream skipped” error saying the credentials or permissions are not good enough. The file only reads data; it does not write anything back to Recruitee.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream page by page and yields each page of records to the sync system. It is used when the system wants to read candidates, offers, or departments from Recruitee.

**Data flow**: It receives an HTTP client, a stream description, and an unused cursor value. It builds the stream URL from the stream’s source object, asks the shared REST helper to fetch numbered pages of up to 100 records, and yields each list of records as it arrives. If Recruitee replies with 401 or 403, meaning unauthorized or forbidden, it changes that low-level HTTP failure into a clearer StreamSkipped error; other HTTP errors continue upward unchanged.

**Call relations**: During a sync, the connector framework calls this method for each Recruitee stream it wants to read. The method delegates the repeated page fetching to the base REST connector’s page-number helper, then hands each page back to the caller. If access is refused, it creates a StreamSkipped error so the larger sync flow can treat that stream as unavailable because of missing permission or invalid credentials.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce sources
Connectors that sync employee records, workers, contracts, payslips, timesheets, time off, teams, and related HR operations data.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `during BambooHR source sync`

BambooHR is an employee-management service, but its API does not behave like one neat table with standard pages. Some endpoints return a list inside an `employees` field, some return a raw list, some need a date range, and employee details require first fetching the directory and then asking for each employee one by one. This file is the adapter that hides those differences.

It defines the BambooHR streams the system can sync, such as the employee directory, detailed employees, time-off requests, timesheet entries, field metadata, and a custom report. The `BambooHRConnector` then builds an HTTP client with the right headers and BambooHR’s required authentication style: HTTP Basic authentication, where the API key is used as the username and `x` is used as the password. It also forces JSON responses, because BambooHR may otherwise default to XML.

The main doorway is `paginate`. Despite the name, BambooHR does not really paginate these endpoints. Instead, `paginate` acts like a switchboard: based on the requested stream, it calls the matching fetch method and yields whatever record batches come back. If BambooHR refuses access with an authorization error, the stream is skipped with a clear explanation rather than crashing the whole connector.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: Creates a stream description for one kind of BambooHR data, such as employees or time-off requests. A stream description tells the sync system what the stream is called, what field identifies each record, and which date fields can be used as cursors.

**Data flow**: It receives a stream name and optional details like the source object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the system’s small recipe for syncing that stream.

**Call relations**: This helper is used while the file is being loaded to build the `BAMBOOHR_STREAMS` list. It hands each completed stream recipe to `StreamSpec` so `BambooHRConnector` can later advertise which BambooHR streams it supports.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to BambooHR. It makes sure every request uses the tenant-specific base URL, asks for JSON, and includes the correct authentication.

**Data flow**: It receives a base URL and a resolved credential. It trims the base URL, prepares timeouts and JSON headers, then either uses a provided transport from an authentication broker or creates Basic authentication from the API key. It returns an `httpx.AsyncClient`, which is the object used to send web requests; if no usable credential is present, it raises an error.

**Call relations**: The broader REST connector calls this when it is setting up a BambooHR sync. This method hands back the ready-to-use HTTP client that the fetch methods later rely on when `paginate` starts reading streams.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right BambooHR fetch routine for the requested stream and yields batches of records to the sync engine. It is called `paginate` because the base connector expects that shape, even though BambooHR usually returns each whole dataset at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching private fetch method, and passes each returned batch onward. If BambooHR returns a permission refusal, it turns that into a `StreamSkipped` message explaining that the key or permissions are not enough.

**Call relations**: This is the connector’s central switchboard. The sync framework asks it for records for a stream; it delegates to `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports`, then yields their results back to the framework.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR’s employee directory. This is the broad list of employees, returned as one collection rather than traditional pages.

**Data flow**: It sends a GET request to the employee directory endpoint, looks for the `employees` list in the response, and yields that list if it is not empty. If BambooHR returns no employees, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. It is also conceptually the first step for detailed employee syncing, because the detailed employee fetch uses the same directory endpoint to discover employee IDs.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches detailed records for individual employees. BambooHR does not provide this as one ready-made list, so the connector first reads the directory and then visits each employee’s detail endpoint.

**Data flow**: It gets the employee directory, loops through each directory row, extracts the employee ID, and requests `/v1/employees/{id}` for that person. For each valid detail response, it makes sure the `id` is present and yields a one-record batch.

**Call relations**: `paginate` calls this for the `employees` stream. It yields one employee at a time so the sync process can checkpoint progress frequently instead of waiting until every employee detail has been fetched.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads time-off requests from BambooHR within a date window. The window lets the connector do a full historical pull on a first run or start from a previous cursor on later runs.

**Data flow**: It receives the HTTP client and an optional cursor, converts the cursor into BambooHR `start` and `end` query parameters, and requests the time-off endpoint. It accepts either a raw list response or a response with records under `requests`, then yields the records if any exist.

**Call relations**: `paginate` calls this for the `time_off_requests` stream. Before making the request, it asks `_date_window_params` to translate the sync cursor into the date range BambooHR requires.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads timesheet entry records from BambooHR within a required date window. This gives the sync system access to tracked work-time data.

**Data flow**: It receives the HTTP client and optional cursor, builds `start` and `end` parameters from that cursor, and requests the timesheet entries endpoint. It accepts either a raw list or a response containing an `entries` list, then yields the records when present.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. Like the time-off fetch, it depends on `_date_window_params` to turn the last known sync point into BambooHR’s expected request format.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR’s field catalog, which describes the available employee fields. This helps the system understand what kinds of data BambooHR can provide.

**Data flow**: It sends a GET request to the metadata fields endpoint. It accepts either a raw list response or a response with a `fields` list, then yields that list if it contains records.

**Call relations**: `paginate` calls this when the requested stream is `meta_fields`. It supplies metadata records back to the same sync path used for normal data streams.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs a predefined BambooHR custom report and reads its rows. This gives the connector a compact way to request a selected set of useful employee fields in one report-shaped response.

**Data flow**: It builds a request body with a report title and a fixed list of fields such as name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to the custom reports endpoint, reads rows from the `employees` field in the response, and yields them if present.

**Call relations**: `paginate` calls this for the `custom_reports` stream. It differs from most other fetch methods because BambooHR requires a POST request with a field list instead of a simple GET request.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the `start` and `end` date parameters required by BambooHR’s time-based endpoints. It turns the sync cursor into the date-only format BambooHR expects.

**Data flow**: It receives an optional cursor. If the cursor is present, it trims it and uses its first 10 characters as the start date, which works for ISO-style timestamps like `2024-05-10T12:00:00Z`; otherwise it starts at `1970-01-01`. It always returns a far-future end date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this before making their API requests. It is the small shared helper that keeps both date-windowed streams using the same cursor-to-date behavior.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `during source sync when reading Deel API data`

Deel is an HR platform, and its API returns data in pages rather than all at once. This file is the read-only connector that knows which Deel objects can be synced and how to ask Deel for them safely, page by page. Without it, the larger system would not know where Deel’s records live, how to request the next batch, or how to resume from a previous sync for records that support updates.

The file first defines a small helper for describing a stream. A stream is one kind of thing to sync, such as contracts or payslips. Each stream says its name, where it appears in Deel’s API, what field uniquely identifies each record, and whether it can be fetched incrementally using an update timestamp. Forms are special because they do not use an update cursor, so they are fully refreshed each time.

The `DeelConnector` then provides the API details: Deel’s base web address, the list of streams, the starting query parameters, and the pagination loop. Pagination works like asking for pages in a book: request 100 records starting at offset 0, then 100 records starting at offset 100, and so on. When a page comes back empty or shorter than 100 records, the connector knows it has reached the end.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one type of Deel data, such as contracts or timesheets. It keeps the stream setup short and consistent so the connector can define all supported Deel objects clearly.

**Data flow**: It receives a stream name and optional details such as the API object name, unique ID field, update timestamp field, and whether the stream is canonical. It fills in sensible defaults, then produces a `StreamSpec`, which is the system’s description of one syncable data stream.

**Call relations**: This is used while the file is being loaded to build the Deel stream list. It hands each prepared stream description to the connector class, which later uses those descriptions during sync.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the first set of query parameters for a Deel API request. It always asks for a fixed page size, and when possible it adds an incremental-sync filter so only recently updated records are requested.

**Data flow**: It receives a stream description and an optional cursor, which is the last saved update point from a previous sync. It starts with `limit: 100`; if there is a cursor and the stream supports update filtering, it adds `updated_after` with that cursor value. It returns the parameter dictionary used for API requests.

**Call relations**: The pagination routine calls this before it starts fetching pages. The returned parameters become the base request settings, and `paginate` adds the changing page offset on top of them for each API call.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls the actual record objects out of Deel’s API response. It protects the rest of the connector from unexpected response shapes by returning only dictionary-like records and ignoring anything else.

**Data flow**: It receives raw response data from the API. If the response is a dictionary with a `data` list inside, it keeps only the list items that are records. If the response itself is already a list, it does the same filtering. If neither shape matches, it returns an empty list.

**Call relations**: The pagination routine calls this after each API request. Its output decides both what records get yielded to the sync system and whether pagination should continue or stop.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for Deel streams. It repeatedly calls Deel’s API for one stream, yielding batches of records until there are no more pages to fetch.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the API path for that stream, prepares base request parameters, then repeatedly adds an `offset` value to request the next page. Each response is converted into records; non-empty batches are yielded outward. If no records come back, or the batch is smaller than the page size, it stops.

**Call relations**: This is the connector’s core handoff point between Deel’s web API and the rest of the sync system. During a sync, the framework calls it for a stream; it uses `_initial_params` to prepare requests and `_extract_records` to clean responses before passing record batches back to the caller.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `source sync`

Rippling is an external HR system, and its API does not send every worker or team at once. It sends one page at a time, with a pointer to the next page if more data exists. This file is the connector that knows Rippling’s rules: which streams exist, what their IDs are, which ones can be read incrementally, and how to follow the API’s “next” links.

The main class, RipplingConnector, is read-only. It does not create or update anything in Rippling. It asks for companies, workers, or teams, receives JSON data, extracts the actual records, and yields them in groups. Workers and teams can use a saved timestamp cursor, meaning “only give me items updated after this time.” Companies do not have that kind of cursor here, so they are refreshed from the beginning.

A useful analogy is a librarian fetching boxes from a storage room. The connector asks for the first box, reads the papers inside, checks whether there is a note pointing to another box, and keeps going until there is no note. If Rippling refuses access with an authorization error, the connector marks that stream as skipped instead of pretending the data is empty.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This function turns Rippling’s “next page” link into a path the connector can request next. It accepts both full web addresses and shorter relative paths, because APIs may return either form.

**Data flow**: It takes a possible next-link string. If there is no link, it returns nothing, meaning pagination should stop. If the link is a full URL, it strips it down to just the path and query string; if it is already a relative path, it returns it unchanged.

**Call relations**: During pagination, RipplingConnector.paginate calls this after each page is read. The result becomes the next request path, so this function is what lets the connector move from page to page without caring whether Rippling returned a full URL or a short path.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the query parameters for the first request to Rippling. It sets the page size and, when possible, adds an incremental sync filter so the connector asks only for recently changed records.

**Data flow**: It receives the stream being read and an optional cursor timestamp. It always creates a limit parameter using the file’s page size. If the stream supports a cursor and a cursor value was supplied, it adds an updatedAfter parameter. It returns the finished parameter dictionary for the first API call.

**Call relations**: RipplingConnector.paginate uses this at the start of a stream read. After the first page, pagination follows Rippling’s next links instead of rebuilding these starting parameters.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This function pulls the actual record objects out of Rippling’s response body. It exists because Rippling may wrap rows in slightly different shapes, such as under a stream-specific key or under a generic data key.

**Data flow**: It receives decoded response data and the stream definition. If the data is a dictionary, it first looks for a list under the stream name, then for a list under data. If the whole response is already a list, it uses that. In every case, it keeps only dictionary-like records and returns them as a list; unknown shapes produce an empty list.

**Call relations**: RipplingConnector.paginate calls this after each API response arrives. The cleaned list it returns is what paginate yields to the rest of the sync pipeline.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It requests pages from Rippling, yields batches of records, follows next-page links, and turns authorization failures into a clear “stream skipped” signal.

**Data flow**: It starts with an HTTP client, a stream definition, and an optional cursor. It builds the first request path and query parameters, asks Rippling for data, extracts records, yields non-empty batches, then follows the next link until no more pages remain. If Rippling returns a 401 or 403 status, meaning the key is invalid or lacks permission, it raises StreamSkipped; other HTTP errors continue upward as real failures.

**Call relations**: The wider source sync system calls this when it wants records for companies, workers, or teams. Inside the loop it relies on _initial_query to prepare the first request, _extract_records to find usable rows, and _next_path to decide whether and where to request the next page.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

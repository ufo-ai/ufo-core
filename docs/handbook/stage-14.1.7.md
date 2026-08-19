# HR and recruiting connectors  `stage-14.1.7`

This stage is a set of behind-the-scenes connectors for HR and recruiting tools. A connector is a small adapter that knows how to talk to one outside service and translate its replies into the system’s common shape. These files are used during sync runs, when the system gathers fresh records and stores them as “recallable pages,” meaning saved batches that can be read again later.

The Ashby, Greenhouse, and Recruitee connectors cover recruiting. They fetch things like candidates, jobs, applications, interviews, offers, departments, and users, then split long API results into streams the sync engine can page through. BambooHR focuses on employee records, time off, timesheets, custom fields, and reports, including several different response formats. Deel reads workforce and payroll-adjacent data such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team records.

Together, these connectors act like plug adapters: each fits a different HR product, but all deliver clean batches of records to the same storage and sync machinery.

## Files in this stage

### Recruiting platforms
Connectors that page through applicant-tracking and recruiting APIs for candidates, jobs, applications, interviews, offers, and related lookup records.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `during Ashby source syncs`

Ashby is a recruiting platform, and its API works a little differently from many web APIs: even list-style reads are made with HTTP POST requests, and large result sets arrive in pages. This file wraps those details so the rest of the system can simply ask for an Ashby stream and receive batches of records.

The file first defines the set of Ashby streams the connector knows about. A stream is a named kind of data, such as candidates or job postings, plus details like the Ashby endpoint path, the record's main identifier, and which timestamp can be used for incremental syncing. Incremental syncing means reading only records that changed since the last successful run, rather than re-reading everything every time.

The AshbyConnector class then provides the actual connection behavior. It builds an HTTP client with Ashby's required authentication: the API key is sent as an HTTP Basic Authorization header, with the key as the username and no password. For normal streams, it repeatedly posts to Ashby's list endpoint, follows Ashby's next cursor when more data exists, and yields each page of records. One stream is special: criteria evaluations are not listed globally, so the connector first lists applications, then asks Ashby for evaluations for each application. Without this file, the system would not know Ashby's endpoints, authentication style, paging rules, or this per-application fan-out.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for a particular Ashby object type. It keeps the stream catalog compact and consistent, so each Ashby resource can be described with its name, endpoint path, main ID field, and timestamp fields.

**Data flow**: It receives a human-readable stream name, an Ashby API path, and optional details such as the primary key and cursor field. It uses those values to build a StreamSpec object, adding shared defaults like createdAt and updatedAt fields. The result is a reusable stream description that the connector later uses to make requests and interpret records.

**Call relations**: This helper is used while the file defines the Ashby stream list. It hands its inputs into StreamSpec.__init__, which creates the formal stream object the connector advertises through its streams_list.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to Ashby, with the correct authentication attached. It matters because Ashby expects API keys to be sent as HTTP Basic authentication, not as a normal bearer token header.

**Data flow**: It receives a base URL and a Credential object. If the credential already has a custom transport, meaning traffic may be routed through an authentication proxy or broker, it leaves that path alone and delegates to the parent connector. Otherwise, it reads the direct API key from the credential, encodes it in the Basic-auth format, creates a new Credential carrying the Authorization header, and asks the parent connector to build the client. If no API key is present, it stops with an error instead of making unauthenticated requests.

**Call relations**: This method is used when the connector is being prepared to make Ashby API calls. It relies on base64.b64encode to produce the Basic-auth token and Credential.__init__ to package the resulting header before handing client creation back to the shared RestConnector behavior.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's traffic director for reading pages from Ashby. It decides whether a stream can use the normal Ashby paging pattern or needs the special per-application criteria-evaluation flow.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor from a previous sync. If the requested stream is application_criteria_evaluations, it ignores the normal endpoint flow and yields pages from the special application fan-out method. For every other stream, it passes the stream and cursor to the default paginator and yields the pages it returns.

**Call relations**: The broader source framework calls this method when it wants records for a stream. This method then calls either AshbyConnector._paginate_application_criteria or AshbyConnector._paginate_default, depending on the stream name, and passes their yielded record batches back upward.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads ordinary Ashby list endpoints page by page. It also supports incremental syncing by sending the saved cursor as Ashby's syncToken on the first request.

**Data flow**: It starts with a request body containing the page size. If a previous cursor exists, it adds that as syncToken so Ashby can return only changed records. It posts to the stream's Ashby endpoint, yields the results if any are present, checks whether Ashby says more data is available, and, if so, repeats with the nextCursor value. The output is a sequence of record batches; the method stops when Ashby reports no more data or fails to provide a next cursor.

**Call relations**: AshbyConnector.paginate calls this for all normal streams. Inside the loop it uses the connector's inherited POST helper to make each Ashby request, then hands each non-empty page of records back to paginate, which passes them on to the sync system.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads application criteria evaluations, which Ashby exposes only by application ID rather than as one global list. It first finds applications, then asks for the criteria evaluations belonging to each one.

**Data flow**: It pages through /application.list to get application records. For each application, it checks that the record is a dictionary and has an id. It then posts that id to /application.listCriteriaEvaluations, copies each returned evaluation, and makes sure the applicationId is present on the row. It yields batches of stamped evaluation records, then continues through application pages until Ashby says there is no more data or gives no next cursor.

**Call relations**: AshbyConnector.paginate calls this only for the application_criteria_evaluations stream. This method uses the connector's inherited POST helper twice in its workflow: first to enumerate applications, then to fetch the child evaluation records for each application.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `during source sync, while reading Greenhouse API pages`

Greenhouse exposes recruiting data through its Harvest API. This file is the adapter that knows Greenhouse’s rules: which web address to call for each kind of record, how to authenticate, how to move through multiple pages of results, and how to fetch smaller child lists that belong to a parent record. Without it, the wider system might know that it wants “candidates” or “jobs”, but it would not know where Greenhouse keeps them or how to ask safely.

The file first defines stream descriptions. A stream is a named kind of data to sync, like “applications” or “job stages”. Some streams are simple top-level lists. Others are nested: for example, to get a candidate’s activity feed, the connector must first list candidates, then ask for the activity feed for each candidate.

Greenhouse uses HTTP Basic authentication, where the API key is sent like a username with an empty password. The connector only applies that directly when the credential is available locally; if an auth broker is in use, the broker injects the authentication instead.

Pagination uses Greenhouse’s `Link` header, which is like a “next page” signpost. If Greenhouse refuses access with a 401 or 403 response, the connector marks that stream as skipped rather than failing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: Builds a small description of one Greenhouse data stream. It records the stream’s name, where it comes from, which field identifies each record, and which date field can be used for incremental syncing.

**Data flow**: It receives stream settings such as the public name, source object path, primary key, and cursor fields. It fills in sensible defaults when details are not supplied, then returns a `StreamSpec`, which is the system’s standard description of a readable stream.

**Call relations**: This helper is used while the module is loaded to define all the Greenhouse streams in one consistent shape. It hands each finished description to the connector’s stream list, so later sync code can ask for streams by name without knowing the raw API details.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the web client used to talk to Greenhouse and adjusts authentication for Greenhouse’s API-key-as-username style. This matters because Greenhouse does not use the usual bearer-token header in the direct credential case.

**Data flow**: It receives a base URL and a resolved credential. It starts from the normal REST client, then, if the credential contains a local key, replaces bearer-style authorization with HTTP Basic authentication using the key as the username and an empty password. It returns the configured async HTTP client.

**Call relations**: The connector framework calls this when preparing to read from Greenhouse. After this client is built, pagination methods use it to make the actual API requests. If a broker is handling authentication, this method leaves the base client’s transport path alone so the broker can inject credentials.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: Chooses the correct query parameter name for incremental syncing. Most Greenhouse streams use `updated_after`, but a few endpoints use a different name.

**Data flow**: It receives a stream name. It looks for a special parameter name for that stream, and if none is listed, falls back to `updated_after`. It returns the parameter name as text.

**Call relations**: The main pagination method calls this when it has a saved cursor value and wants Greenhouse to return only newer records. This keeps the special cases in one small place instead of scattering them through the request-building code.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Decides how to read pages for a requested Greenhouse stream. It knows whether the stream is a simple endpoint or a nested child list, adds incremental-sync filters when possible, and turns permission refusals into clean stream skips.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It checks whether the stream must be fetched per parent; if so, it delegates to the parent-child paginator. Otherwise it finds the simple API path, adds `per_page=500`, adds a cursor filter when appropriate, and yields each page of records. If Greenhouse returns 401 or 403, it raises `StreamSkipped`; other errors continue as real failures.

**Call relations**: This is the connector’s main read path. The sync runner asks it for pages of a stream. It calls `_cursor_param` to name incremental filters, `_paginate_link_header` for ordinary paged endpoints, and `_paginate_per_parent` for nested streams such as candidate activity feeds or job openings.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a Greenhouse endpoint page by page using the API’s `Link` header. The `Link` header is Greenhouse’s way of saying, “here is the next page, if there is one.”

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST machinery to fetch pages at the configured page size, then yields each page of records unchanged.

**Call relations**: Both the main `paginate` method and the parent-child paginator rely on this helper whenever they need to walk through a Greenhouse list. It is the small reusable bridge between Greenhouse’s pagination style and the connector’s stream output.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches nested Greenhouse data that only exists under another record, such as job openings under each job or permissions under each user. It also labels each child record with the parent’s ID so the relationship is not lost downstream.

**Data flow**: It receives the parent list path, a child path template, and the field name where the parent ID should be stored. It pages through all parents, reads each parent’s `id`, fetches that parent’s child pages, adds the parent ID to each child record when possible, and yields the child pages.

**Call relations**: The main `paginate` method calls this for streams listed as per-parent streams. This helper repeatedly uses `_paginate_link_header`: first to list parents, then again to read each parent’s child collection. It acts like a librarian who first finds every folder, then copies the papers inside each folder while writing the folder number on each paper.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `source sync`

Recruitee is an online hiring tool, and its data lives behind a web API. This file defines a small connector that knows which Recruitee lists are worth syncing and how to walk through them safely. Think of it like a librarian who knows which shelves to visit and how to keep asking for the next cart of books until there are no more.

The connector declares three streams: candidates, offers, and departments. A stream is just one kind of record the sync system can pull. Candidates and offers are marked as main, or canonical, streams; departments are available too, but not treated as a main record type. Each stream uses an `id` field as its stable identifier.

Recruitee uses page-number pagination, meaning the connector asks for page 1, then page 2, and so on, with up to 100 records per page. The file does not store credentials itself. Authentication is supplied elsewhere by the runner, which helps avoid leaking or duplicating secrets here.

One important safety choice is that the base URL is blank by default. Recruitee URLs include a company-specific part, so the sync must provide the correct tenant URL. If it does not, the connector fails instead of accidentally calling the wrong place. If Recruitee rejects access with a 401 or 403 response, the stream is skipped with a clear message rather than crashing unclearly.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream a page at a time and yields each page of records to the sync system. It is used when the system is reading candidates, offers, or departments from Recruitee.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. The cursor is not used here because these Recruitee streams are full-refresh: each run reads the whole list from the beginning. The function builds the stream path, asks for pages of up to 100 records, and yields each list of records as it arrives. If Recruitee answers with 401 or 403, meaning the key is invalid or does not have permission, it changes that low-level web error into a clear `StreamSkipped` result for this stream; other web errors are passed upward unchanged.

**Call relations**: During a Recruitee source sync, the runner asks this connector to paginate each declared stream. The function relies on the shared REST connector paging behavior to do the repeated page requests. If access is refused, it creates a `StreamSkipped` error so the wider sync flow can understand that this stream was deliberately skipped because of permissions rather than because of a mysterious failure.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce systems
Connectors that sync employee, contract, payroll-adjacent, timesheet, time-off, company, worker, and team records from HR operations platforms.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `source sync`

BambooHR is a human resources service, and its API does not behave like one neat, standard list. Some endpoints return a list inside an "employees" field, some return a bare list, some require a date range, and detailed employee records must be fetched one person at a time. This file is the adapter that hides those differences.

The file first defines the BambooHR streams, which are the named sets of data the system can sync, such as the employee directory, time-off requests, and custom reports. The BambooHRConnector then creates an HTTP client with the right tenant URL, JSON headers, timeouts, and BambooHR’s Basic authentication style, where the API key is used as the username.

When the sync engine asks for a stream, paginate acts like a traffic director. It looks at the stream name and sends the request to the matching fetch method. Each fetch method calls the right BambooHR endpoint and yields records in batches. For date-based data, it builds a start and end window from the previous sync cursor, so later runs can resume from where they left off. If BambooHR rejects access with a permission or authentication error, the file raises StreamSkipped, meaning this stream is skipped clearly instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a StreamSpec, which is a small description of one BambooHR data stream. It keeps the stream definitions short and consistent, so each stream can say its name, key field, cursor field, and whether it is considered a main canonical stream.

**Data flow**: It receives a stream name and optional details such as the BambooHR source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a StreamSpec object that the connector uses later to know what data can be synced.

**Call relations**: This helper is used while the module is being loaded to build the BambooHR stream list. It hands the completed stream descriptions to the connector class through BAMBOOHR_STREAMS, so the wider sync system can discover what BambooHR data is available.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to BambooHR. It sets the base address, JSON headers, time limits, and authentication in the exact form BambooHR expects.

**Data flow**: It receives a base URL and a resolved credential. It trims extra slashes from the URL, prepares timeouts and headers, then either uses a provided transport from an auth proxy or creates Basic authentication from the API key. It returns an asynchronous HTTP client ready to make requests; if no usable credential is present, it raises an error.

**Call relations**: The parent REST connector calls this when it is preparing to sync. After this client is made, paginate and the fetch methods use it to call BambooHR endpoints.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main router for reading BambooHR streams. Even though it is called paginate, BambooHR does not use normal page-by-page pagination here; this method chooses the right fetch routine for the requested stream and yields whatever batches that routine produces.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It checks the stream name, calls the matching fetch method, and passes each returned batch onward. If BambooHR responds with a 401 or 403 refusal, it turns that into a StreamSkipped message explaining that the key or permissions are not enough.

**Call relations**: The sync engine calls paginate when it wants records for a stream. paginate then delegates to _fetch_directory, _fetch_employees, _fetch_time_off, _fetch_timesheets, _fetch_meta_fields, or _fetch_custom_reports. It is the single doorway from the generic sync machinery into the BambooHR-specific fetching code.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR’s employee directory, which is the broad list of employees. It extracts the actual employee rows from BambooHR’s response and yields them as one batch.

**Data flow**: It receives the HTTP client, requests the employee directory endpoint, reads the "employees" list from the returned JSON data, and yields that list if it is not empty. It does not change any stored state.

**Call relations**: paginate calls this when the requested stream is employees_directory. The result is handed straight back to the sync engine as the directory stream’s records.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches full detail for each employee. BambooHR does not provide all detail in the directory list, so this method first reads the directory, then asks for each employee’s individual record.

**Data flow**: It receives the HTTP client, downloads the directory, loops through each directory row, and looks for an employee id. For every valid id, it requests that employee’s detail endpoint, makes sure the detail record contains the id, and yields a one-record batch. Rows without usable ids are skipped.

**Call relations**: paginate calls this for the employees stream. It uses the directory as a starting list, then fans out into one request per employee so the sync system receives detailed employee records and can checkpoint progress frequently.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches time-off requests within a date window. BambooHR requires both a start and an end date for this endpoint, so this method prepares that window before asking for records.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into start and end request parameters, calls the time-off requests endpoint, then accepts either a plain list response or a response with records under "requests". If any records are found, it yields them as one batch.

**Call relations**: paginate calls this for the time_off_requests stream. Before making the request, it relies on _date_window_params to translate the sync cursor into BambooHR’s required date parameters.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches timesheet entries within a date window. Like time-off data, BambooHR requires a start and end date, so this method uses the current sync cursor to choose the window.

**Data flow**: It receives the HTTP client and an optional cursor. It builds date parameters, requests the timesheet entries endpoint, and reads records either from a plain list response or from an "entries" field. If records exist, it yields them as one batch.

**Call relations**: paginate calls this for the timesheet_entries stream. It shares the date-window helper with _fetch_time_off because both BambooHR endpoints need the same kind of start and end parameters.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR’s field catalog, which describes the available employee fields. This is useful when the system needs to understand what kinds of data BambooHR can provide.

**Data flow**: It receives the HTTP client, requests the metadata fields endpoint, and reads records either from a plain list response or from a "fields" field. If there are records, it yields them as one batch.

**Call relations**: paginate calls this when syncing the meta_fields stream. The records it yields go back to the sync engine just like normal business data, even though they describe fields rather than employees or time entries.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This requests a BambooHR custom report with a fixed set of useful employee fields. It gives the sync a recallable report-style view of employee data without requiring a separate report to be created by hand in BambooHR.

**Data flow**: It receives the HTTP client, builds a JSON request body containing a report title and a list of desired fields, and sends it with a POST request. It then reads the returned employee rows from the "employees" field and yields them as one batch if any are present.

**Call relations**: paginate calls this for the custom_reports stream. Unlike the simple fetch methods that use GET requests, this one sends a POST request because BambooHR’s custom report endpoint expects the requested fields in the request body.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This converts a previous sync position into the date range BambooHR requires for time-based endpoints. It gives fresh syncs a very early start date, and later syncs a start date based on the saved cursor.

**Data flow**: It receives an optional cursor, which may be an ISO-style timestamp or date string. If the cursor is present and not blank, it keeps only the first 10 characters, matching BambooHR’s YYYY-MM-DD date format. It returns a dictionary with "start" and "end" values, using 1970-01-01 as the default start and 2100-01-01 as a far-future end.

**Call relations**: _fetch_time_off and _fetch_timesheets call this before making their API requests. It keeps the date-window rule in one place so both streams ask BambooHR for the right range in the same way.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `source sync run`

Deel is an HR platform, and its API returns information in small pages rather than all at once. This file is the adapter that knows which Deel objects to fetch, how to ask for the next page, and how to pull the useful records out of Deel’s response shape. Without it, the system would not know how to sync Deel data at all, or how to avoid repeatedly downloading everything when an incremental update is possible.

The file first defines a small helper for describing a Deel stream. A stream is one kind of object to sync, such as contracts or payslips. Each stream says what Deel endpoint object it maps to, which field identifies a record, and whether it can be updated incrementally using an `updated_at`-style timestamp. Forms are special because they do not have that update cursor, so they are fetched in full each run.

`DeelConnector` then supplies the actual reading behavior. It uses Deel’s REST v2 API, starting each request with a limit of 100 records. If the stream supports incremental syncing and the system has a previous cursor, it adds `updated_after` so Deel only returns newer changes. It then walks through pages using an `offset`, like turning pages in a catalog: 0, 100, 200, and so on. Each response is cleaned down to a list of record dictionaries and yielded to the sync system. There is deliberately no write path here; this connector only reads from Deel.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one type of Deel object, such as contracts or tasks. It keeps the stream setup short and consistent so the list of Deel streams is easy to read.

**Data flow**: It receives a stream name plus optional details like the Deel API object name, primary key field, update cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the system’s small description of what to sync and how to identify changes.

**Call relations**: This is used while the file is loaded to build the fixed list of Deel streams. It hands the completed stream description to the connector class, which later uses those descriptions when deciding what API path and update parameters to use.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the starting query parameters for a Deel API request. It always asks for up to 100 records and, when possible, asks Deel to return only records updated after the last sync point.

**Data flow**: It takes a stream description and an optional cursor value, which is usually a saved timestamp from a previous sync. It creates a parameter dictionary with `limit: 100`; if there is both a cursor and the stream supports cursor-based updates, it also adds `updated_after`. The result is a ready-to-use set of request parameters before paging details are added.

**Call relations**: The pagination loop calls this once at the start of reading a stream. `paginate` then copies these base parameters for each page and adds the changing `offset` value before making each HTTP request.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls the actual record objects out of a Deel API response. It protects the rest of the sync flow from unexpected response shapes by returning only dictionary-like records.

**Data flow**: It receives raw response data from the API. If the data is a dictionary with a `data` list inside, it keeps only the list items that are dictionaries. If the response itself is already a list, it does the same filtering there. If neither shape fits, it returns an empty list.

**Call relations**: After `paginate` fetches a page from Deel, it passes the raw response here. The cleaned list of records then decides what happens next: yield the records, stop if none were found, or continue to another page if the page was full.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Deel streams. It repeatedly requests pages from Deel until there are no more records to sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It builds the Deel REST v2 path, prepares base query parameters, then loops through offsets in steps of 100. For each page, it fetches data from Deel, extracts valid records, yields those records to the caller, and stops when the page is empty or smaller than the maximum page size.

**Call relations**: The broader source-sync framework calls this when it needs records for one Deel stream. Inside the loop it relies on `_initial_params` to prepare request parameters and `_extract_records` to clean the API response before handing batches of records back to the framework.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `source sync pagination`

Rippling exposes business data through a web API, but it does not send everything at once. Like a long search result split across many pages, each response contains some rows and may include a “next” link to the following page. This file defines a Rippling connector that knows which Rippling objects are available, how to ask for them, and how to keep following those pages until there are no more.

The file declares three streams: companies, workers, and teams. Workers and teams can be fetched incrementally, meaning the connector can ask only for records changed after a saved timestamp. Companies do not have that documented update cursor here, so they are read as a full refresh.

The main class, RipplingConnector, inherits shared REST behavior from the project’s RestConnector. It supplies Rippling’s base web address, stream definitions, and the pagination rules that are specific to Rippling. It also normalizes Rippling’s slightly flexible response shapes: records might be under a key like “workers”, under a generic “data” key, or returned directly as a list.

If Rippling rejects the request with an authorization error, the connector raises StreamSkipped. That tells the wider sync system that this stream could not be read because the token is missing permission or is invalid, rather than pretending the stream is empty.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s “next page” link into the path the HTTP client should request next. Rippling may return either a full web address or just a relative path, so this function makes both forms usable.

**Data flow**: It receives a next-page link or nothing. If there is no link, it returns nothing, meaning pagination is finished. If the link is a full URL, it keeps only the path and query string, such as “/workers?page=2”, because the connector already knows Rippling’s base address. If the link is already relative, it returns it unchanged.

**Call relations**: During pagination, RipplingConnector.paginate calls this after each response to decide where to go next. The helper uses urlparse to inspect whether the link is a full URL, then hands back a clean path for the next request.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query options for the first API request for a stream. It always asks for a fixed page size and, when possible, adds an incremental filter so only recently updated records are requested.

**Data flow**: It receives the stream definition and an optional saved cursor value, usually a timestamp from a previous sync. It starts with a limit of 100 records per page. If the stream supports a cursor and a cursor value was provided, it adds an “updatedAfter” value. It returns the finished query parameter dictionary for the first request.

**Call relations**: RipplingConnector.paginate calls this once before it starts fetching pages. After the first request, paginate clears these parameters because later pages are driven by Rippling’s own “next” link.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls the actual record rows out of a Rippling API response. It protects the rest of the connector from small differences in response shape by accepting the common layouts Rippling may return.

**Data flow**: It receives raw response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream’s own name, such as “workers” or “teams”. If that is not present, it looks for a generic “data” list. If the whole response is already a list, it uses that. In every case, it keeps only items that are dictionaries, and returns a clean list of records.

**Call relations**: RipplingConnector.paginate calls this after every HTTP response. The cleaned records are what paginate yields outward to the sync runner, while non-record shapes are quietly ignored.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader loop for a Rippling stream. It requests the first page, yields batches of records, follows the “next” link, and stops when Rippling says there are no more pages.

**Data flow**: It receives an asynchronous HTTP client, a stream definition, and an optional cursor timestamp. It builds the first request path and query parameters, sends a GET request through the shared REST connector, extracts records from the returned data, and yields each non-empty batch. After the first page, it follows the response’s “next” link until no next path remains. If Rippling returns a 401 or 403 refusal, it changes that low-level HTTP error into a StreamSkipped message explaining that the token is invalid or lacks permission.

**Call relations**: The wider source-sync runner calls this when it wants records for companies, workers, or teams. Inside the loop, it relies on _initial_query to prepare the first request, _extract_records to find usable rows in each response, and _next_path to decide whether another page should be fetched. When authorization fails, it raises StreamSkipped so the caller can skip that stream cleanly instead of crashing the whole sync for an expected permission problem.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

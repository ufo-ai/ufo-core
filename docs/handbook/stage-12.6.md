# HR and recruiting source connectors  `stage-12.6`

This stage is the system’s set of “adapters” for HR and recruiting tools. It runs during data syncing, when the system reaches out to outside services, reads their records, and turns them into a common stream of data that the rest of the code can store, search, or process. Each connector knows the rules of one vendor’s web API, meaning the online doorway used to request data.

The Ashby connector reads recruiting records such as candidates, jobs, applications, interviews, and offers. The Greenhouse connector does the same for Greenhouse Harvest, covering many hiring endpoints page by page. The Recruitee connector brings in candidates, job offers, and departments from Recruitee lists. BambooHR focuses on employee and HR records. Deel reads workforce data such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team data.

Together, these files act like translators at different service desks. Each speaks to one external system, handles its paging, and hands back orderly batches for the shared sync machinery.

## Files in this stage

### Recruiting platform connectors
Applicant-tracking and recruiting sources expose candidates, jobs, applications, interviews, offers, and related hiring records.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `during source sync`

Ashby exposes recruiting data through a web API, but it does not return everything in one request. Each list call returns one page of results, plus a pointer to the next page if more data exists. This file wraps that pattern so the rest of the system can simply ask for a stream like "candidates" or "jobs" and receive batches of records.

The file first defines the Ashby streams the connector knows about. A stream is a named collection of records, such as applications or users, with details like its API path, its main ID field, and whether it can be synced incrementally using an "updatedAt" timestamp. Incremental sync means reading only records changed since the last run, like checking only mail that arrived after yesterday.

The `AshbyConnector` then adds Ashby-specific behavior. It builds the correct authentication header, because Ashby uses HTTP Basic authentication with the API key as the username and an empty password. It also knows how to page through normal list endpoints. One stream is special: `application_criteria_evaluations` is not listed on its own. The connector must first list applications, then ask Ashby for evaluation records for each application ID. Without this file, the system would not know Ashby's endpoint names, login style, paging rules, or this special per-application lookup.

#### Function details

##### `_stream`  (lines 28–44)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream description for an Ashby API endpoint. It keeps the stream catalog short and consistent, so each Ashby object can be declared with its name, API path, ID field, and optional update cursor.

**Data flow**: It receives a stream name, an Ashby path, and optional details such as the primary key and cursor field. It packages those values into a `StreamSpec`, which is the system's standard description of a readable source stream. The result is a stream definition that the connector later uses to know what endpoint to call and how to identify records.

**Call relations**: This function is used while building the `ASHBY_STREAMS` list at import time. It hands each completed `StreamSpec` to the connector's stream catalog, and it creates those specs by calling `StreamSpec.__init__`.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 80–88)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Ashby, with Ashby's unusual authentication format applied. It makes sure a plain API key is converted into the Basic authentication header Ashby expects.

**Data flow**: It receives a base URL and a credential. If the credential already has a custom transport, it leaves that path alone and lets the parent connector build the client. Otherwise, it reads the API key from `credential.bearer`, encodes `api_key:` using Base64, and builds a new credential containing an `Authorization: Basic ...` header. It returns an asynchronous HTTP client ready to make Ashby requests, or raises an error if no API key is available.

**Call relations**: This method fits into the connector setup step, when the broader REST connector needs a client before making requests. It uses `base64.b64encode` to format Ashby's Basic auth token and creates a `Credential` carrying the final header before delegating client construction to the parent connector.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 90–98)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main paging entry point for Ashby streams. It decides whether a stream can use the normal Ashby list paging pattern or needs the special application-by-application lookup.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. If the stream is `application_criteria_evaluations`, it ignores the normal cursor path and yields pages produced by the special fan-out method. For every other stream, it passes the stream and cursor into the default paging method and yields each batch of records it returns.

**Call relations**: The broader sync system calls this when it wants records for a stream. This function acts like a traffic director: ordinary streams are handed to `AshbyConnector._paginate_default`, while the special criteria-evaluation stream is handed to `AshbyConnector._paginate_application_criteria`.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 100–121)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal Ashby list endpoint page by page. It supports both full backfills and incremental reads by sending Ashby's `syncToken` when the system has a saved cursor.

**Data flow**: It starts with a request body containing the page size. If a cursor is supplied, it adds that as `syncToken`, meaning Ashby should return records changed since that token. It repeatedly posts to the stream's Ashby endpoint, yields any returned `results`, and follows `nextCursor` while Ashby says more data is available. It stops when there is no more data or when Ashby does not provide a next cursor.

**Call relations**: This method is called by `AshbyConnector.paginate` for all ordinary streams. It relies on the connector's inherited `_post` request helper to send each HTTP POST, then hands batches of records back up to `paginate`, which in turn feeds the sync pipeline.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 123–159)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads criteria evaluation records, which Ashby only exposes underneath individual applications. It first finds application IDs, then asks for evaluation records for each one.

**Data flow**: It pages through `/application.list` using the normal Ashby cursor pattern. For each application record, it extracts the application ID. It then posts that ID to `/application.listCriteriaEvaluations`, copies each returned evaluation record, and ensures the record includes the matching `applicationId`. It yields batches of stamped evaluation records, then continues to the next application page until Ashby has no more applications to list.

**Call relations**: This method is called only by `AshbyConnector.paginate` when the requested stream is `application_criteria_evaluations`. It performs a fan-out flow: one application listing request leads to many per-application detail requests, and the resulting evaluation batches are passed back to the main pagination path.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `source sync`

Greenhouse exposes recruiting data through many separate web API paths. This file is the adapter that knows those paths, how to authenticate, how to page through results, and how to deal with nested data such as “interviews for this application” or “openings for this job.” Without it, the system would not know how to fetch Greenhouse records in a reliable, repeatable way.

Most of the file is a catalog of streams. A stream is a named type of data the sync system can ask for, such as candidates, jobs, applications, or scorecards. Some streams are simple top-level lists. Others are child lists: first the connector fetches parent records, such as jobs, then asks Greenhouse for each job’s openings or stages. When it does that, it stamps each child record with the parent id, like putting a label on a folder so later readers know where the page came from.

The connector also handles Greenhouse’s details. It uses HTTP Basic authentication, where the API key is sent as the username and the password is empty. It follows Greenhouse pagination through HTTP Link headers, which are web response headers that point to the next page. For incremental syncs, it sends a “changed after this time” filter when a stream supports one. If Greenhouse replies that the API key is not allowed to read a stream, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_stream`  (lines 65–79)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description in one consistent way. It is used to define what each Greenhouse data stream is called, what Greenhouse object it comes from, which field uniquely identifies records, and whether the stream supports incremental syncing.

**Data flow**: It receives a stream name plus optional details such as the source object name, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults, then returns a StreamSpec object that the sync framework can understand.

**Call relations**: During module setup, the file repeatedly calls this helper to build the Greenhouse stream catalog. Inside the helper, it hands the normalized details to StreamSpec so the shared source-sync system can later use those stream definitions.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 224–233)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the web client used to talk to Greenhouse, with the authentication style Greenhouse expects. Greenhouse Harvest wants HTTP Basic authentication, meaning the API key is placed in the username slot and the password is blank.

**Data flow**: It receives the Greenhouse base URL and a resolved credential. First it asks the base connector to create a normal HTTP client. If the credential contains an API key directly, it replaces the usual bearer-token header with Basic authentication using that key. It returns the prepared async HTTP client.

**Call relations**: The source framework calls this when it needs a client for a Greenhouse sync. It relies on the base RestConnector for the standard client setup, then adjusts only the Greenhouse-specific authentication detail by using httpx.BasicAuth.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 236–239)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This chooses the query parameter name Greenhouse expects for incremental syncing on a given stream. Most streams use updated_after, but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It checks the small exception table for that stream and returns the special parameter name if one exists; otherwise it returns the default, updated_after.

**Call relations**: GreenhouseConnector.paginate calls this when it is building a request for a stream that has a cursor value. Its result becomes part of the API query so Greenhouse returns only records newer than the last saved sync point.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 241–272)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading routine for a Greenhouse stream. Given a stream and an optional cursor, it yields Greenhouse records in pages so the rest of the sync system can process them without loading everything at once.

**Data flow**: It receives an HTTP client, a stream description, and optionally the last cursor value from a previous sync. If the stream is a child stream, it fetches parent records first and then each parent’s child records. If it is a simple stream, it builds the Greenhouse path and query parameters, including page size and cursor filtering when available. It yields lists of records page by page. If Greenhouse returns 401 or 403, meaning unauthorized or forbidden, it turns that into a StreamSkipped signal instead of a hard failure.

**Call relations**: The broader sync engine calls this when it wants records for one Greenhouse stream. This function decides which pagination path to use: it hands child streams to GreenhouseConnector._paginate_per_parent, simple streams to GreenhouseConnector._paginate_link_header, and uses GreenhouseConnector._cursor_param to name cursor filters correctly. When access is refused, it raises StreamSkipped so the run can record a skipped stream.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 274–281)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through a normal Greenhouse list endpoint one page at a time. It follows the HTTP Link header, which is Greenhouse’s way of saying where the next page is.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST connector for pages using the configured page size of 500 records. Each page it receives is yielded onward unchanged.

**Call relations**: GreenhouseConnector.paginate uses this for ordinary top-level streams. GreenhouseConnector._paginate_per_parent also uses it twice: once to list parent records and again to list each parent’s child records. It delegates the low-level Link-header mechanics to the shared REST connector helper.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 283–304)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads nested Greenhouse data that only exists under a parent record, such as openings under a job or permissions under a user. It preserves the relationship by adding the parent id to each child record when that field is missing.

**Data flow**: It receives the parent API path, a child path template containing the parent id, and the field name where the parent id should be stored. It fetches parent pages, takes each parent’s id, fetches that parent’s child pages, stamps each child dictionary with the parent id if needed, and yields each child page.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is one of the per-parent streams. This function repeatedly calls GreenhouseConnector._paginate_link_header so both the parent collection and each child collection use the same Greenhouse pagination behavior.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `during source sync, when fetching Recruitee records from the API`

Recruitee is a hiring platform, and this connector is the project’s read-only doorway into it. Without this file, the system would not know which Recruitee records to fetch, where to fetch them from, or how to move through Recruitee’s paged API responses.

The file defines three streams of data: candidates, offers, and departments. A stream is just one kind of list the sync can copy from an outside service. Candidates and offers are marked as main, or “canonical,” records, while departments are included as supporting data. Each stream uses Recruitee’s record ID as its unique key.

Recruitee returns results in numbered pages, like reading a long document one sheet at a time: page 1, page 2, and so on, with up to 100 records per page. The connector asks for those pages until the API returns a smaller final page, which means there is no more data.

One important safety choice is that the connector has no default base URL. Recruitee URLs include a company-specific tenant ID, so the wider sync runner must provide the correct full API prefix. This avoids accidentally calling the wrong company’s endpoint. The connector also does not store credentials itself; authentication is supplied elsewhere. If Recruitee rejects access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing unclearly.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream in pages and yields each page of records to the sync system. It also turns permission failures into a clear “skip this stream” signal when the API says the credentials are invalid or lack access.

**Data flow**: It receives an HTTP client, a stream description such as candidates or offers, and an unused cursor value. It builds the Recruitee path for that stream, asks the shared REST helper to fetch numbered pages of up to 100 records, and passes each list of records onward. If Recruitee responds with 401 or 403, it raises a StreamSkipped error with a human-readable reason; other HTTP errors are allowed to keep bubbling up as real failures.

**Call relations**: During a sync, the base REST connector calls this method when it needs records for a Recruitee stream. The method delegates the repeated page fetching to the shared page-number helper, then yields the results back to the sync pipeline. If access is refused, it creates a StreamSkipped exception so the broader sync can report that this particular stream could not be read because of credentials or permissions.

*Call graph*: calls 1 internal fn (__init__).


### Workforce HR connectors
HR and workforce sources expose employee, contractor, company, team, payroll, timesheet, and task records.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `during source sync`

BambooHR is an HR system, and its API does not behave like many list-style web APIs. Some endpoints return everything at once, some wrap records inside a field like "employees", and some require a date range even when the sync just wants "newer than last time". This file hides those differences behind one connector, so the rest of the project can ask for streams such as employee directory, time off requests, timesheet entries, field metadata, or a custom employee report without knowing BambooHR's quirks.

The file first defines the streams the connector offers. Each stream is like a shelf label in a library: it tells the sync system what kind of records can be requested and which field identifies or advances them. The BambooHRConnector then builds an HTTP client with the right base address, timeouts, JSON headers, and BambooHR's Basic authentication style, where the API key is used as the username.

The central method, paginate, is a dispatcher. Even though the name suggests normal pagination, BambooHR usually returns one full response, so paginate chooses the right fetch helper for the requested stream and yields records in batches. If BambooHR refuses access with a 401 or 403 status, the connector reports that the stream should be skipped instead of treating it like an ordinary crash.

#### Function details

##### `_stream`  (lines 30–44)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one BambooHR data source, such as employees or time off requests. This keeps the stream list short and consistent instead of repeating the same setup details each time.

**Data flow**: It receives a stream name and optional details such as the BambooHR object name, primary key, cursor field, and whether it is a canonical stream. It fills in defaults when details are missing, then returns a StreamSpec object that the sync system can use to identify and read that stream.

**Call relations**: This helper is used while the file is being loaded to build BAMBOOHR_STREAMS. It hands the finished stream descriptions to the connector class, which later uses them to decide what BambooHR data can be synced.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 62–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the web client used to talk to BambooHR. It makes sure requests go to the right tenant URL, ask for JSON instead of BambooHR's XML default, and use the correct authentication.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, sets connection and read time limits, adds JSON headers, and then chooses how to authenticate: either by using a provided transport from an auth broker, or by using the API key as a Basic Auth username with "x" as the password. It returns an httpx AsyncClient ready to make requests, or raises an error if no usable credential is present.

**Call relations**: This method is part of the connector setup path. It creates the AsyncClient, Timeout, and BasicAuth objects that the later fetch methods depend on when they call BambooHR endpoints.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 79–114)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct BambooHR fetch routine for the requested stream and yields batches of records. It gives the rest of the sync system one common way to read several differently shaped BambooHR endpoints.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching private fetch method, and passes through each batch that method yields. If BambooHR responds with 401 or 403, it turns that into a StreamSkipped message explaining that the key or permissions are not sufficient; other HTTP errors are allowed to keep failing normally.

**Call relations**: This is the main traffic director for the file. When the sync system asks for a stream, paginate sends employee directory requests to _fetch_directory, employee detail to _fetch_employees, time-based streams to _fetch_time_off or _fetch_timesheets, metadata to _fetch_meta_fields, and custom report data to _fetch_custom_reports.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 116–122)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR's employee directory endpoint. This provides the broad list of employees in the company directory.

**Data flow**: It receives the prepared HTTP client, sends a GET request to the directory endpoint, looks for the "employees" list in the returned JSON, and yields that list if it is not empty. If there are no employees in the response, it yields nothing.

**Call relations**: paginate calls this when the requested stream is employees_directory. The directory data is also the same kind of starting point that _fetch_employees uses internally for per-employee detail.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 124–140)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches detailed records for each employee, not just the lighter directory listing. It uses the directory as a roster, then visits each employee's detail page one by one.

**Data flow**: It receives the HTTP client, gets the employee directory, and loops through each directory row. For each row that is a dictionary and has an id, it requests /v1/employees/{id}. If the detail response is a dictionary, it makes sure the id is present and yields that one employee as a single-item batch.

**Call relations**: paginate calls this for the employees stream. It deliberately yields after each employee detail fetch, which lets the wider sync process make progress and checkpoint frequently instead of waiting for every employee to finish.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 142–149)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads time off requests from BambooHR using the date window BambooHR requires. It supports incremental syncs by starting the window from the previous cursor when one exists.

**Data flow**: It receives the HTTP client and an optional cursor. It converts the cursor into start and end parameters with _date_window_params, sends a GET request for time off requests, then accepts either a plain list response or a dictionary containing "requests". If records are found, it yields them as one batch.

**Call relations**: paginate calls this when syncing the time_off_requests stream. This function relies on _date_window_params to translate the sync cursor into BambooHR's required start/end query parameters.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 151–158)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR timesheet entries for a required date range. Like the time off reader, it can begin from the last saved cursor so the sync does not have to start from scratch every time.

**Data flow**: It receives the HTTP client and an optional cursor. It builds BambooHR date parameters with _date_window_params, requests the timesheet entries endpoint, and then handles either a plain list response or a dictionary with an "entries" list. If any entries are present, it yields them.

**Call relations**: paginate calls this for the timesheet_entries stream. It shares the date-window helper with _fetch_time_off because both BambooHR endpoints need the same kind of start and end parameters.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 160–166)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR's field catalog, which describes the available employee fields. This helps the system learn what fields BambooHR exposes, rather than only reading employee rows.

**Data flow**: It receives the HTTP client, sends a GET request to the metadata fields endpoint, and accepts either a direct list or a dictionary containing "fields". If there are field records, it yields them as a batch.

**Call relations**: paginate calls this when the requested stream is meta_fields. It is a simple endpoint-specific reader that normalizes BambooHR's response shape for the sync system.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 168–190)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs a BambooHR custom report for a fixed set of useful employee fields. This is useful when the desired employee data is best requested as a report instead of through the normal employee endpoint.

**Data flow**: It receives the HTTP client, builds a JSON request body with a report title and selected fields such as name, email, job title, department, supervisor, hire date, and status. It sends that body with a POST request to the custom reports endpoint, reads the returned "employees" rows, and yields them if present.

**Call relations**: paginate calls this for the custom_reports stream. Unlike most other fetch methods in this file, it uses a POST request because BambooHR expects the report definition to be sent in the request body.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 193–203)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: Turns the sync cursor into the start and end dates BambooHR requires for time-based endpoints. It gives fresh syncs a very old start date so they can collect historical data.

**Data flow**: It receives an optional cursor. If the cursor has text, it strips whitespace and takes the first 10 characters, matching a YYYY-MM-DD date from an ISO-style timestamp; otherwise it uses 1970-01-01. It returns a dictionary with that start date and a far-future end date of 2100-01-01.

**Call relations**: _fetch_time_off and _fetch_timesheets call this before making their BambooHR requests. It keeps the date-window rule in one place so both time-based streams behave the same way.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `during source sync, when reading records from Deel`

Deel is an external HR service, and its data lives behind a REST API, which is a web interface where the system asks for data using HTTP requests. This file is the read-only connector for that API. Without it, the project would not know which Deel objects can be synced, how to ask Deel for them, or how to keep fetching later pages of results.

The file first defines the Deel streams: contracts, forms, payslips, timesheets, and tasks. A stream is one category of records to import. Most streams can be synced incrementally, meaning the connector asks only for records updated after the last saved time. Forms are different: they do not use an update cursor here, so they are fully refreshed each run.

The main class, `DeelConnector`, gives the shared REST connector framework the Deel-specific details: the service name, the base web address, the available streams, and the paging rules. Deel returns results in batches of up to 100 records using `limit` and `offset` query parameters. Think of this like reading a long report 100 rows at a time: after each full page, the connector moves the bookmark forward and asks for the next page. It stops when Deel returns no records or a shorter final page.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of Deel object. It saves repeated setup code by filling in common defaults, such as using `id` as the main record identifier and `updated_at` as the usual incremental sync field.

**Data flow**: It receives a stream name and optional details such as the Deel API object path, primary key, cursor field, and whether the stream is canonical. It uses those values to build a `StreamSpec`, which is the small description object the connector framework uses later to know what to request and how to track progress.

**Call relations**: This function is used while the file is being loaded to build the `DEEL_STREAMS` list. It hands each completed stream description to the rest of the connector through `DeelConnector.streams_list`.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the starting set of query parameters for a Deel API request. It always asks for a page of up to 100 records, and when possible it adds the saved update cursor so the sync can fetch only newer changes.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It creates a parameter dictionary with `limit` set to the page size. If there is a cursor and the stream supports cursor-based syncing, it adds `updated_after` with that cursor. The result is a dictionary ready to be sent as web request parameters.

**Call relations**: `DeelConnector.paginate` calls this at the start of reading a stream. The returned parameters become the base request settings, and `paginate` adds a changing `offset` value for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls usable record objects out of Deel's API response. It protects the rest of the sync from unexpected response shapes by returning only dictionary-like records and ignoring anything else.

**Data flow**: It receives raw response data from the API. If the response is a dictionary with a `data` list, it filters that list down to dictionary records. If the response is already a list, it does the same filtering there. If neither shape fits, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each web request. The extracted records are what `paginate` yields back to the connector framework as the next batch of synced data.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function reads one Deel stream page by page. It keeps asking Deel for the next batch of records until there are no more records to fetch.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the Deel API path, prepares the base parameters, then starts at offset zero. For each loop, it adds the current offset, makes a GET request through the shared REST connector, extracts records from the response, and yields the records as a batch. If the batch is empty or smaller than 100 records, it stops; otherwise it advances the offset by 100 and continues.

**Call relations**: The shared source-sync machinery calls `paginate` when it needs records for a Deel stream. Inside the loop, `paginate` relies on `_initial_params` to prepare request parameters and `_extract_records` to turn the raw API response into clean record batches.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `request handling`

Rippling is an outside service, so the system needs a small adapter that knows Rippling’s rules: where to call, how pages are linked, what each list of records is called, and how to ask for only newer data when possible. This file provides that adapter through `RipplingConnector`.

It defines three readable streams: companies, workers, and teams. Workers and teams can be read incrementally, meaning the connector can ask Rippling for records updated after a saved time. Companies do not have that kind of cursor here, so they are read as a full refresh.

The main work happens in `paginate`. It starts at the API path for the stream, adds a page size, optionally adds an `updatedAfter` filter, then repeatedly calls Rippling. Each response may contain records under a stream-specific key, under a generic `data` key, or directly as a list. The connector normalizes those shapes into plain lists of record dictionaries. If Rippling returns a `next` link, the connector follows it like turning to the next page in a book.

If Rippling refuses access with a 401 or 403 status, the connector raises `StreamSkipped` instead of treating it like a normal crash. That tells the wider sync runner that this stream could not be read because the token is invalid or missing permission.

#### Function details

##### `RipplingConnector._next_path`  (lines 48–59)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s `next` page link into the path the HTTP client should request next. It accepts both full web addresses and already-short relative paths.

**Data flow**: It receives a `next_link`, which may be missing, may be a full URL, or may be a relative path. If it is missing, it returns nothing. If it is a full URL, it keeps only the path and query string, because the connector already knows Rippling’s base address. If it is already a relative path, it returns it unchanged.

**Call relations**: `paginate` calls this after each response to decide whether there is another page to fetch. Internally it uses `urlparse` to split a full URL into its parts before handing the cleaned path back to the paging loop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 62–66)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first request to a Rippling stream. It always asks for a fixed page size and, when possible, asks only for records updated after a saved cursor value.

**Data flow**: It receives a stream description and an optional cursor, which is usually a timestamp from the last sync. It starts with `limit` set to the connector’s page size. If the stream supports a cursor and a cursor value was provided, it adds `updatedAfter`. It returns the finished parameter dictionary for the first API call.

**Call relations**: `paginate` calls this once at the start of reading a stream. After the first request, `paginate` stops using these parameters because Rippling’s `next` link already contains the information needed to continue.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 69–79)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls actual record objects out of the different response shapes Rippling may return. It protects the rest of the connector from having to care whether records are under `workers`, `teams`, `companies`, `data`, or are returned directly as a list.

**Data flow**: It receives the decoded response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream name, then under `data`. If the response itself is a list, it uses that. In every case, it keeps only items that are dictionaries, because those are usable records, and returns them as a list. If nothing matches, it returns an empty list.

**Call relations**: `paginate` calls this after each API response. The cleaned list it returns is what `paginate` yields to the rest of the sync system as one batch of Rippling records.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 81–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for a Rippling stream. It walks through all available API pages, yields batches of records, and turns permission failures into a clear “skip this stream” signal.

**Data flow**: It receives an asynchronous HTTP client, a stream description, and an optional cursor. It builds the starting API path and first query parameters, requests a page, extracts records from the response, and yields non-empty batches. Then it reads the response’s `next` link, converts it into the next path, and repeats until there is no next page. If Rippling returns 401 or 403, it raises `StreamSkipped`; other HTTP errors are passed upward unchanged.

**Call relations**: The wider source-sync runner calls `paginate` when it needs records for companies, workers, or teams. `paginate` relies on `_initial_query` to start correctly, `_extract_records` to normalize each response, and `_next_path` to keep following pages. When access is refused, it creates a `StreamSkipped` error so the runner can continue without pretending the stream succeeded.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

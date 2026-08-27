# HR and Recruiting Providers  `stage-16.9`

This stage is part of the system’s data-gathering layer. Its job is to connect to HR and recruiting services and turn their online API responses into steady streams of records that the rest of the sync system can store, search, or process. An API is a service’s structured doorway for requesting data.

Each file is an adapter, like a plug shaped for one vendor. The Ashby reader pulls recruiting records such as candidates, jobs, applications, interviews, offers, and users from Ashby’s paged API. BambooHR reads employee and HR records from several BambooHR endpoints and presents them as named streams. Deel reads workforce records such as contracts, forms, payslips, timesheets, and tasks. Greenhouse does a similar job for Greenhouse Harvest, covering candidates, jobs, applications, interviews, offers, and users across many endpoints. Recruitee reads hiring data such as candidates, job offers, and departments. Rippling reads companies, workers, and teams. Together, these providers hide vendor differences so the rest of the system sees consistent batches of records.

## Files in this stage

### Ashby recruiting ingestion
Introduces recruiting pipeline reads through Ashby's paged API for candidates, jobs, applications, interviews, offers, and users.

### `extensions/sources/ufo_ext_sources/providers/ashby.py`

`io_transport` · `source sync`

Ashby exposes recruiting data through a web API, but it does not return everything at once. It returns one page at a time, with a marker that says where to continue. This file is the adapter that knows Ashby's rules: which endpoints exist, how to authenticate, how to ask for the next page, and how to do incremental reads using a saved cursor.

The file first defines the list of Ashby streams the system can read. A stream is one kind of data, like candidates or jobs. Each stream records the Ashby endpoint path, the field that uniquely identifies each record, and, when available, the timestamp field used to pick up only changed records later.

The `AshbyConnector` class then supplies the Ashby-specific behavior for a general REST connector. It builds an HTTP client using Ashby's required Basic authentication style, where the API key is used as the username and the password is empty. It also knows how to page through Ashby's POST-based list endpoints.

Most streams use the same paging loop: send a request, yield the returned records, then continue while Ashby says more data is available. One stream is unusual: application criteria evaluations must be fetched by first listing applications, then asking for evaluations for each application ID. Without this file, the system would not know how to reliably walk through Ashby's API or resume syncs from a previous point.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream definition for one Ashby data type. It keeps the stream list compact and consistent, so each Ashby endpoint is described in the same shape.

**Data flow**: It receives a friendly stream name, the Ashby API path, and optional details such as the unique ID field and update-time field. It packages those details into a `StreamSpec`, which is the system's standard description of a readable source stream. The result is used later by the connector to know what to request and how to identify records.

**Call relations**: This helper is used while the file is being loaded to build the Ashby stream catalog. It hands each completed stream description to the connector through the `ASHBY_STREAMS` list, and internally it calls `StreamSpec.__init__` to create the standard stream object.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Ashby, with the right authentication attached. Ashby expects Basic authentication, so the function converts the stored API key into the correct header format.

**Data flow**: It receives the base API URL and a credential. If the credential already contains a custom transport, it lets the parent connector build the client unchanged. Otherwise, it reads the API key from the credential, encodes it as `api_key:` using Base64 text encoding, and returns a client with an `Authorization: Basic ...` header. If there is no API key, it raises an error because requests would fail anyway.

**Call relations**: The general REST connector calls this when it needs a network client for an Ashby sync. This Ashby-specific version prepares the credential in Ashby's required form, then hands the actual client creation back to the parent connector. It uses `base64.b64encode` for the Basic-auth encoding and creates a new `Credential` carrying the finished header.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main paging dispatcher for Ashby streams. It decides whether a stream can use the normal Ashby paging pattern or needs special per-application fetching.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. If the stream is `application_criteria_evaluations`, it starts the special application-by-application process. For every other stream, it starts the normal paging process and passes along the cursor. In both cases, it yields pages of records back to the caller.

**Call relations**: The broader sync machinery calls this when it wants records for a particular Ashby stream. This function acts like a traffic director: ordinary streams are sent to `_paginate_default`, while the unusual criteria-evaluation stream is sent to `_paginate_application_criteria`.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal Ashby list endpoint one page at a time. It also supports incremental syncing by sending Ashby's `syncToken` when the system has a saved cursor.

**Data flow**: It starts with a request body containing the page size. If a cursor is provided, it adds it as `syncToken`, which asks Ashby for records changed since that point. It sends a POST request to the stream's endpoint, yields any records in `results`, then checks whether Ashby says more data is available. If so, it sends the next request with Ashby's `nextCursor`; if not, or if no next cursor is provided, it stops.

**Call relations**: `paginate` calls this for almost every Ashby stream. This function performs the repeated request-and-yield loop and relies on the connector's `_post` helper from the parent REST connector to actually send each HTTP request.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function handles the one Ashby stream that cannot be fetched as a simple list. Criteria evaluations belong to individual applications, so the function first finds applications and then fetches evaluations for each one.

**Data flow**: It pages through `/application.list` to get application records. For each application record that has an ID, it sends a second POST request to `/application.listCriteriaEvaluations` with that application ID. It copies each returned evaluation, makes sure the `applicationId` is present on the row, groups the evaluations into pages, and yields them. It continues through application pages until Ashby reports there is no more application data.

**Call relations**: `paginate` calls this only for the `application_criteria_evaluations` stream. It is a fan-out flow, like looking up every order first and then fetching the line items for each order. It uses the connector's `_post` helper for both the application list request and each per-application detail request.

*Call graph*: called by 1 (paginate).


### Employee and contractor records
Covers HR and workforce-adjacent data sources for employees, contracts, forms, payslips, timesheets, and tasks.

### `extensions/sources/ufo_ext_sources/providers/bamboohr.py`

`io_transport` · `source sync`

BambooHR is an HR service, and this connector is the bridge between BambooHR and this project's source-sync system. Without this file, the system would not know how to sign in to BambooHR, which BambooHR URLs to call, or how to turn BambooHR's different response shapes into batches of records.

The file first defines the BambooHR streams the system can read: the employee directory, detailed employee records, time-off requests, timesheet entries, metadata fields, and a custom employee report. A stream is simply a named kind of data that can be synced.

The main class, BambooHRConnector, builds an HTTP client for BambooHR. HTTP is the web protocol used for API calls. BambooHR requires Basic authentication, where the API key is sent like a username and the password is the literal letter "x". It also requires an `Accept: application/json` header, because BambooHR may otherwise return XML, a different data format.

BambooHR does not use normal page-by-page pagination. Instead, most endpoints return everything for that endpoint at once, and each endpoint wraps its records differently. The `paginate` method acts like a switchboard: based on the stream name, it calls the right helper. Some helpers fetch one list. The employee-detail helper first fetches the directory, then asks BambooHR for each employee one by one. Time-based streams build a start/end date window from the saved cursor so repeat syncs can pick up from the right date.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a stream description for one kind of BambooHR data. It keeps the stream list short and consistent, so each stream has a name, source object, key field, and optional date fields.

**Data flow**: It receives stream settings such as the stream name, primary key, cursor field, and whether it is a canonical stream. It fills in sensible defaults, such as using the stream name as the source object when no separate source object is given. It returns a StreamSpec, which is the small description object the sync system uses later to know what it can read.

**Call relations**: This helper is used while the file is loaded to build the BambooHR stream catalog. It hands those StreamSpec objects to the connector class through `BAMBOOHR_STREAMS`, so later sync code can ask for streams by name.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to BambooHR. It sets time limits, JSON headers, the tenant-specific base URL, and the right authentication method.

**Data flow**: It receives a BambooHR base URL and a credential. It trims any trailing slash from the URL, sets headers asking for JSON, and creates an HTTP client. If the credential already contains a custom transport, it preserves that. If the credential contains a direct API key, it uses BambooHR's Basic authentication pattern with the key as the username and "x" as the password. If there is no usable authentication data, it raises an error instead of making unauthenticated calls.

**Call relations**: The broader REST connector machinery calls this when it is ready to connect to BambooHR. The client it returns is then passed into `paginate` and the fetch helpers, which use it to make the actual API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading BambooHR streams. Because BambooHR's endpoints do not all behave the same way, this function chooses the correct fetch method for the requested stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It looks at the stream name, calls the matching fetch helper, and yields each batch of records that helper produces. If BambooHR replies with a 401 or 403 refusal, meaning the key is invalid or lacks permission, it turns that into `StreamSkipped` so the sync can skip that stream with a clear reason. Other HTTP errors are allowed to bubble up.

**Call relations**: The sync system calls `paginate` when it wants records for a BambooHR stream. `paginate` then hands the work to `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports` depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads BambooHR's employee directory. The directory is the basic list of employees returned by BambooHR in one response.

**Data flow**: It sends a GET request to the employee directory endpoint. It expects BambooHR to return a JSON object with an `employees` list. If that list has records, it yields the list as one batch; if it is empty, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. It is the simplest read path: one API call in, one batch of directory rows out.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads detailed employee records, not just the lighter directory rows. It first uses the directory to learn which employee IDs exist, then fetches each employee's full record.

**Data flow**: It starts by requesting the employee directory. From each directory row, it extracts an employee ID. For every valid ID, it sends a separate GET request to `/v1/employees/{id}`. If the detail response is a dictionary-like record, it makes sure the record has the employee ID and yields that one employee as a one-record batch.

**Call relations**: `paginate` calls this for the `employees` stream. It depends on the directory endpoint as a starting list, then fans out into one request per employee. Yielding one employee at a time lets the larger sync process make progress and checkpoint promptly.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads time-off requests from BambooHR, such as vacation or leave requests. It asks BambooHR for requests inside a date window.

**Data flow**: It receives the HTTP client and an optional cursor. It converts the cursor into `start` and `end` request parameters using `_date_window_params`, then sends a GET request to the time-off endpoint. BambooHR may return either a plain list or an object containing a `requests` list, so the function accepts both shapes. If records are found, it yields them as one batch.

**Call relations**: `paginate` calls this for the `time_off_requests` stream. This function relies on `_date_window_params` to build the date range BambooHR requires before it makes the API call.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads timesheet entries from BambooHR. Like time-off requests, BambooHR requires a start and end date for this data.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into date-window parameters, calls the timesheet endpoint, and accepts either a plain list response or an object with an `entries` list. If there are entries, it yields them as one batch.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. It uses `_date_window_params` in the same way as the time-off reader, so both date-window endpoints follow the same rules.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads BambooHR's field catalog. The field catalog describes the fields BambooHR knows about, which can help the system understand available employee data.

**Data flow**: It sends a GET request to the metadata fields endpoint. BambooHR may return either a list directly or an object containing a `fields` list. The function normalizes those possibilities into a records list and yields it if it is not empty.

**Call relations**: `paginate` calls this for the `meta_fields` stream. It is a single-call fetch helper, similar to the directory reader, but aimed at metadata rather than employee records.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks BambooHR to generate a custom report with a fixed set of employee fields. It is used when the sync wants a report-shaped view of employee information.

**Data flow**: It builds a JSON request body naming the report and listing fields such as employee ID, display name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to the custom reports endpoint. It then reads the returned `employees` list and yields it as a batch if records exist.

**Call relations**: `paginate` calls this for the `custom_reports` stream. Unlike the other readers, it uses a POST request because BambooHR expects the desired report fields to be sent in the request body.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This small helper builds the date range BambooHR requires for time-off and timesheet requests. It turns the saved sync cursor into a BambooHR-friendly start date.

**Data flow**: It receives an optional cursor, which may be a full timestamp. If the cursor has text, it takes the first 10 characters, matching the `YYYY-MM-DD` date format BambooHR expects. If there is no cursor, it starts at `1970-01-01` so a fresh sync can collect everything. It always returns a dictionary with that `start` date and a far-future `end` date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this before contacting BambooHR. It gives both helpers the same simple date-window behavior, so they do not each need to repeat that cursor-to-date logic.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/providers/deel.py`

`io_transport` · `source sync runs`

Deel is an HR and payroll service, and its API returns information in small pages rather than all at once. This file is the adapter that knows Deel’s rules: which record types are available, where to ask for them, how to include an update cursor, and how to move through pages using a limit and offset. Without it, the broader source-sync system would not know how to fetch Deel records reliably.

The file first defines a helper for creating stream descriptions. A stream is one kind of thing to sync, like contracts or payslips. Each stream says what API object it maps to, what field uniquely identifies each record, and whether it can be synced incrementally using an updated timestamp. Most Deel streams use `updated_at`, so later syncs can ask only for records changed after a known point. Forms do not, so they are fully refreshed each run.

`DeelConnector` then provides the actual reading behavior. It sets Deel’s base API address, builds the first query parameters, pulls records out of Deel’s response shape, and keeps requesting pages until there are no more records. It is read-only: it fetches data from Deel but does not write anything back.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description for one Deel record type, such as contracts or tasks. It keeps the stream list short and consistent, so each stream is defined with the same defaults unless it needs something special.

**Data flow**: It receives a stream name plus optional details like the API object name, the unique key field, the update-time field, and whether the stream is considered canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the system’s small recipe for how to sync that kind of record.

**Call relations**: This function is used while building the file’s list of Deel streams. It hands its collected settings to `StreamSpec.__init__`, which creates the stream description that `DeelConnector` later exposes to the rest of the source framework.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the first set of query parameters to send to Deel for a stream. It decides whether the request should ask for all records or only records updated after a saved cursor.

**Data flow**: It receives a stream description and an optional cursor value, usually a timestamp from a previous sync. It always starts with a page size limit of 100. If both a cursor exists and the stream supports cursor-based updates, it adds `updated_after` with that cursor. It returns the parameter dictionary used for API requests.

**Call relations**: `DeelConnector.paginate` calls this before it starts paging through Deel results. The returned parameters become the base request settings, and `paginate` adds the changing offset for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function safely pulls usable records out of a Deel API response. It protects the sync from unexpected response shapes by returning only dictionary-like records and ignoring anything else.

**Data flow**: It receives raw response data from Deel. If the response is a dictionary with a `data` list inside, it keeps only the items in that list that are records. If the whole response is already a list, it also keeps only record items. If neither shape matches, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each API request. Its output tells `paginate` whether there are records to yield and whether the paging loop should continue.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function walks through all pages of one Deel stream and yields batches of records. It is the main read loop that turns Deel’s paginated REST API into chunks the sync system can process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the Deel API path for that stream, asks `_initial_params` for the base query parameters, then starts at offset zero. For each loop, it requests one page, uses `_extract_records` to cleanly pull out records, yields the records if any exist, and stops when a page is empty or smaller than the page size. Otherwise it increases the offset by 100 and asks for the next page.

**Call relations**: This is the method the connector relies on when a Deel stream needs to be read. It calls `_initial_params` once to prepare the request and `_extract_records` after every response so the rest of the sync flow receives clean batches rather than raw API payloads.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### Recruiting suite providers
Adds broader hiring-platform readers for Greenhouse and Recruitee recruiting objects and lists.

### `extensions/sources/ufo_ext_sources/providers/greenhouse.py`

`io_transport` · `source sync / API pagination`

Greenhouse exposes recruiting data through a web API, but each endpoint has its own path, permissions, paging rules, and sometimes its own date filter name. This file is the adapter that hides those differences. Without it, the rest of the system would not know where to ask Greenhouse for each kind of data, how to move through pages of results, or how to skip streams the API key is not allowed to read.

The file first defines a catalog of streams. A stream is one kind of data the sync can read, like candidates or job stages. Some are simple top-level lists. Others are child lists that only make sense under a parent item, like a candidate's activity feed or a job's openings. For those child streams, the connector first reads the parent list, then asks Greenhouse for each parent's children, and stamps each child record with the parent id so its origin is not lost.

Greenhouse uses HTTP Basic authentication, where the API key is sent as the username and the password is empty. The connector only adds that directly when the resolved credential contains the key locally; otherwise an auth proxy may inject credentials for it.

Paging follows Greenhouse's Link header, which is like a “next page” signpost in the response. Incremental streams can also send a date cursor, meaning “only give me records after this time.” If Greenhouse returns 401 or 403 for a stream, this file reports that stream as skipped rather than failing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: Creates a stream description for one Greenhouse data type. The rest of the connector uses these descriptions to know the stream name, API object name, primary key, date fields, and whether it is one of the main canonical streams.

**Data flow**: It receives naming and field choices, such as a stream name and cursor field. It fills in sensible defaults, then returns a StreamSpec object that records how that stream should be treated during sync.

**Call relations**: This helper is used while the module is being loaded to build the Greenhouse stream catalog. It hands each completed StreamSpec to the connector through the ALL_STREAMS list.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Greenhouse and adjusts authentication when the API key is available locally. Greenhouse expects Basic auth, so this replaces the usual bearer-token style header when needed.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent RestConnector to create the standard async HTTP client. If the credential contains a bearer value, it treats that value as the Greenhouse API key, installs Basic auth with an empty password, removes any Authorization header left over from the base client, and returns the client.

**Call relations**: This fits into the setup step before any Greenhouse requests are made. The wider RestConnector machinery calls it when preparing a client; after that, pagination methods use the returned client to make API calls.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: Chooses the query parameter name Greenhouse expects for incremental syncing. Most streams use updated_after, but a few Greenhouse endpoints use a different name.

**Data flow**: It receives a stream name. It checks the small override table for special cases, and returns either the special parameter name or the default updated_after.

**Call relations**: GreenhouseConnector.paginate calls this only when it has a cursor value to send. Its result becomes part of the API request parameters so Greenhouse can filter records server-side.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Decides how to fetch pages for a requested Greenhouse stream. It covers simple streams, child-per-parent streams, incremental date filtering, and permission-related skips.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. If the stream is a child stream, it delegates to the per-parent paginator. Otherwise it looks up the stream's direct API path, builds request parameters including page size and optional cursor filter, then yields each page returned by Link-header pagination. If Greenhouse says the key is unauthorized or forbidden, it raises StreamSkipped so the sync records a skip instead of crashing the whole run.

**Call relations**: This is the main read path used by the sync engine for each Greenhouse stream. It calls _cursor_param to name date filters, _paginate_link_header for ordinary endpoints, and _paginate_per_parent for nested endpoints.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through a Greenhouse endpoint one page at a time using the response's Link header. The Link header is the API's way of saying where the next page lives.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It passes those to the shared RestConnector page walker with Greenhouse's page size, then yields each list of records it gets back.

**Call relations**: GreenhouseConnector.paginate uses this for normal top-level streams. GreenhouseConnector._paginate_per_parent also uses it twice: once to read parents, and again to read each parent's child records.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches nested Greenhouse data that must be requested separately for each parent record. For example, it can read every job first, then fetch openings for each job.

**Data flow**: It receives a parent API path, a child path template, and the name of the field where the parent id should be stored. It pages through parents, takes each parent's id, fetches that parent's child pages, adds the parent id to each child record if it is missing, and yields the child pages onward.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is listed as a per-parent stream. This function relies on _paginate_link_header for both parent and child page walking, then hands stamped child pages back to paginate.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/recruitee.py`

`io_transport` · `source sync read phase`

Recruitee is a recruiting tool, and this connector is the bridge between Recruitee and this project’s source-sync system. Its job is read-only: it fetches records from Recruitee, but never creates or changes anything there.

The file defines three streams of data: candidates, offers, and departments. A stream is just one kind of list the system can pull from an outside service. Each stream says which Recruitee object to ask for and which field uniquely identifies each record.

Recruitee’s API returns lists in numbered pages, much like search results with page 1, page 2, and so on. This connector asks for up to 100 records at a time and keeps going until Recruitee returns a shorter page, which means there is probably no next full page to fetch.

One important safety choice is that the connector has no default web address. Recruitee API addresses include a company-specific tenant ID, so the sync runner must provide the correct full base URL. That avoids accidentally calling the wrong company’s endpoint.

Authentication is also kept outside this file. The connector relies on the wider system’s authorization proxy to provide access. If Recruitee replies with “unauthorized” or “forbidden,” the connector skips that stream with a clear error instead of crashing unclearly.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream in batches. It is used when the sync system wants all records for candidates, offers, or departments, one page at a time.

**Data flow**: It receives an HTTP client, a stream description, and an unused cursor value. It builds the correct Recruitee path for that stream, asks for numbered pages of up to 100 records, and yields each page as a list of record dictionaries. If Recruitee refuses access with a 401 or 403 status, it changes that low-level web error into a clear “stream skipped” result explaining that the credentials or permissions are not good enough.

**Call relations**: During a Recruitee sync, the broader source framework calls this method for each declared stream. The method delegates the repeated page fetching to the shared REST connector paging helper, then passes each page back upward to the sync pipeline. If Recruitee rejects a stream, it creates a StreamSkipped exception so the larger run can understand that this particular stream was unavailable because of access permissions.

*Call graph*: calls 1 internal fn (__init__).


### Workforce organization records
Finishes with Rippling reads for companies, workers, and teams as organizational workforce records.

### `extensions/sources/ufo_ext_sources/providers/rippling.py`

`io_transport` · `request handling`

Rippling is an external HR-style system, and its API does not return everything in one response. Instead, it gives one page of results at a time, plus a “next” link when more pages are available. This file defines a Rippling connector: a small adapter that knows which Rippling streams exist, how to ask for them, how to continue through pages, and how to recognize usable records inside each response.

The connector exposes three streams: companies, workers, and teams. Workers and teams can be read incrementally, meaning the system can ask only for records updated after a saved timestamp. Companies do not have that cursor in this connector, so they are read as a full refresh.

The main flow starts with a path such as `/workers`, adds a page limit, and optionally adds `updatedAfter` when a cursor is available. After each API call, it extracts record dictionaries from the response, yields them as a batch, then follows Rippling’s `next` link if there is one. A `next` link may be a full URL or just a relative path, so the connector normalizes it before the next request.

If Rippling responds with 401 or 403, meaning the token is invalid or lacks permission, the connector raises `StreamSkipped` instead of treating it as an ordinary crash. This lets the wider sync system skip that unavailable stream cleanly.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s “next page” link into a request path the connector can use. It accepts either a full URL or a relative path, because APIs often mix these shapes.

**Data flow**: It receives a `next_link`, which may be missing, a full web address, or a path. If it is missing, it returns nothing. If it is a full URL, it uses URL parsing to keep only the path and query string, such as `/workers?cursor=abc`. If it is already a relative path, it returns it unchanged.

**Call relations**: During pagination, `RipplingConnector.paginate` calls this after each response to decide where to request the next page. This helper does the small cleanup step so the main loop can simply keep following paths until there are no more.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first Rippling API request. It always asks for a fixed page size, and for incremental streams it can also ask for records updated after a saved cursor.

**Data flow**: It receives the stream definition and an optional cursor value. It starts with `limit` set to the connector’s page size. If the stream has a cursor field and a cursor was provided, it adds `updatedAfter` with that cursor. It returns the completed dictionary of query parameters.

**Call relations**: At the start of `RipplingConnector.paginate`, this function prepares the first request. After that first request, pagination follows Rippling’s `next` links, so the main loop clears these parameters and lets the returned next path drive the later requests.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the actual record rows inside a Rippling response. It protects the rest of the sync from response-shape differences by accepting records under the stream name, under a generic `data` key, or directly as a list.

**Data flow**: It receives raw response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream name, such as `workers`; if not found, it looks for a list under `data`. If the whole response is already a list, it uses that. In every case, it keeps only items that are dictionaries and returns them as a list of records; if nothing matches, it returns an empty list.

**Call relations**: Each time `RipplingConnector.paginate` receives a page from Rippling, it calls this helper to separate useful records from the surrounding API wrapper. The returned list is what `paginate` yields to the rest of the source-sync pipeline.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main reading loop. It calls Rippling one page at a time, yields batches of records, and follows the API’s `next` link until the stream is finished.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the starting API path and first query parameters, requests a page, extracts records, yields non-empty batches, then replaces the path with the normalized `next` link from the response. If Rippling denies access with 401 or 403, it turns that into `StreamSkipped`; other HTTP errors are allowed to keep failing normally.

**Call relations**: The wider REST source framework calls this method when it needs records from a Rippling stream. Inside the loop, it relies on `_initial_query` to prepare the first request, `_extract_records` to pull rows out of each response, and `_next_path` to continue paging. When access is refused, it hands a clear `StreamSkipped` signal back to the sync framework so the stream can be skipped for permission-related reasons.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

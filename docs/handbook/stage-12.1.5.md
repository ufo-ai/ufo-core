# HR, Recruiting, and Workforce Connectors  `stage-12.1.5`

This stage is the set of “readers” for HR and recruiting tools. It is used during the main sync work, when the system reaches out to outside services and copies their latest people-related records into a common flow. Each file is an adapter, like a plug shape for one vendor’s socket. Ashby, Greenhouse, and Recruitee focus on hiring data: candidates, jobs, applications, interviews, offers, departments, and users. BambooHR focuses on employee operations, including employee profiles, time off, timesheets, field definitions, and reports. Deel reads workforce and contractor data such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team records. These services expose data through web APIs, meaning structured requests over the internet. The connector files know which API addresses to call, how to request the next page when results are split into chunks, and how to turn each service’s different response format into steady batches of records that the wider sync system can process the same way.

## Files in this stage

### Ashby recruiting ingestion
Defines Ashby hiring-platform streams and safe API pagination for recruiting objects.

### `extensions/sources/ufo_ext_sources/providers/ashby.py`

`io_transport` · `during source sync pagination`

Ashby stores recruiting information such as candidates, jobs, applications, interviews, offers, users, and lookup lists like departments or locations. This connector turns those remote Ashby records into streams the larger system can read page by page. Without it, the system would not know Ashby's endpoint names, its unusual POST-based listing style, or how to authenticate with an Ashby API key.

The file first defines a helper, `_stream`, which creates a standard description of one Ashby stream: its name, API path, main ID field, and optional update-time field used for incremental syncing. The `ASHBY_STREAMS` list is the catalog of Ashby data this connector exposes.

`AshbyConnector` then fills in the behavior. It sets Ashby's base URL, converts an API key into the Basic Authorization header Ashby expects, and provides pagination. Pagination means asking for records in chunks, like reading a long book one page at a time. Most streams use the same pattern: send a POST request with a limit, optionally include a sync token to ask for changed records, yield returned records, then continue while Ashby says more data is available.

One stream is special: criteria evaluations are not listed directly. The connector must first list applications, then ask Ashby for evaluations for each application ID. That fan-out behavior is kept separate so the normal streams stay simple.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one Ashby endpoint. This lets the rest of the connector treat candidates, jobs, users, and other Ashby objects in a consistent way even though they come from different API paths.

**Data flow**: It receives a friendly stream name, the Ashby API path, and optional details such as the primary ID field and update timestamp field. It packages those choices into a `StreamSpec`, which is a small description object the source framework can later use to know what to request and how to identify records.

**Call relations**: This helper is used while building the file's Ashby stream catalog. Each call produces one stream definition, and those definitions are attached to `AshbyConnector` so the broader source framework knows which Ashby data sets are available.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Ashby and makes sure authentication is in the form Ashby expects. Ashby wants HTTP Basic authentication, where the API key is used like a username and the password is blank.

**Data flow**: It receives a base URL and a credential. If the credential already has a custom transport, it leaves that path to the parent connector. Otherwise, it reads the API key from the credential, encodes it as a Basic authentication token, creates a new credential containing the Authorization header, and asks the parent connector to build the actual client. If no API key is present, it stops with an error instead of making unauthenticated requests.

**Call relations**: The source framework calls this when preparing to contact Ashby. This method adapts the project's general credential shape to Ashby's specific authentication rule, then hands client creation back to the shared REST connector machinery.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a stream and yields batches of records. Most Ashby streams use the normal list pattern, but application criteria evaluations need extra per-application requests.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name. For the special criteria-evaluation stream, it delegates to the fan-out paginator; for all other streams, it delegates to the default paginator. In both cases, it passes batches of records onward as they are found.

**Call relations**: The shared source framework calls `paginate` when it wants records for a stream. This method acts like a traffic director: ordinary streams go to `_paginate_default`, while the special nested stream goes to `_paginate_application_criteria`.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a normal Ashby list endpoint one page at a time. It supports incremental syncing by sending Ashby's `syncToken` when the framework has a saved cursor.

**Data flow**: It starts with a request body containing a page size. If a cursor is available, it adds that as the sync token so Ashby can return changed records. It posts to the stream's Ashby endpoint, takes the `results` list from the response, yields it if it is not empty, and then checks whether Ashby says more data is available. If there is another cursor from Ashby, it uses that for the next request; otherwise it stops.

**Call relations**: `paginate` sends all ordinary streams here. This function repeatedly uses the connector's POST request helper to fetch each page, then hands record batches back to `paginate`, which in turn feeds the wider sync process.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads criteria evaluations, which Ashby exposes indirectly under each application rather than as one simple list. It first finds applications, then asks for the evaluations belonging to each application.

**Data flow**: It pages through `/application.list` to get application records. For each application that has an ID, it posts that ID to `/application.listCriteriaEvaluations`. It copies each returned evaluation into a new row and makes sure the row includes the `applicationId`, so downstream readers know which application it belongs to. It yields non-empty groups of stamped evaluation rows, then continues through application pages until Ashby has no more.

**Call relations**: `paginate` calls this only for the `application_criteria_evaluations` stream. It performs a two-step walk: list parent applications first, then fetch child evaluation records for each one, returning those child records to the same sync pipeline used by normal streams.

*Call graph*: called by 1 (paginate).


### Core HR operations ingestion
Reads employee, contract, time, pay, and task data from HR operations platforms.

### `extensions/sources/ufo_ext_sources/providers/bamboohr.py`

`io_transport` · `source sync`

BambooHR is an HR system, and this connector is the bridge from BambooHR into this project’s source-sync framework. Its job is read-only: it does not create or update anything in BambooHR. Without this file, the system would not know which BambooHR endpoints to call, how to authenticate, or how to turn BambooHR’s response shapes into streams of records.

The file first defines the BambooHR streams, which are the named groups of data the sync can ask for. Some are direct lists, like the employee directory. Others need extra steps: full employee details are fetched by first reading the directory, then visiting each employee’s own detail endpoint. Time-off and timesheet data require a date window, so the connector builds a start and end date from the saved cursor, which is the sync’s bookmark from a previous run.

BambooHR does not paginate in the usual “page 1, page 2” way. Instead, each endpoint usually returns everything for that request at once. The central `paginate` method therefore acts more like a switchboard: it looks at the requested stream name and sends the work to the right helper. Authentication uses HTTP Basic authentication, with the API key as the username and the literal password `x`. The connector also forces JSON responses because BambooHR may otherwise return XML. If BambooHR refuses access with a 401 or 403 status, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a `StreamSpec`, which is the system’s description of one BambooHR data stream. It keeps the stream definitions short and consistent by filling in common defaults like the primary key field.

**Data flow**: It receives a stream name and optional details such as the source object name, cursor field, and whether the stream is canonical. It combines those values with defaults, then returns a `StreamSpec` object that the connector later advertises as something it can sync.

**Call relations**: This function is used while the file is being loaded to build `BAMBOOHR_STREAMS`. It hands the finished stream descriptions to the `BambooHRConnector` class through its `streams_list` setting.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method builds the HTTP client used to talk to BambooHR’s API. It sets time limits, JSON headers, and the right authentication so later fetch methods can simply make requests.

**Data flow**: It receives a BambooHR base URL and a resolved credential. It trims the URL, prepares headers that ask for JSON, and creates an async HTTP client. If the credential contains a special proxy transport, it uses that unchanged; otherwise, if it contains an API key, it sends that key with Basic authentication. If no usable authentication is present, it raises an error.

**Call relations**: The wider source framework calls this when starting a BambooHR sync. The client it returns is then passed into `paginate` and the fetch helpers, which use it for all API calls.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing point for reading BambooHR streams. Even though the name says “paginate,” BambooHR usually returns whole datasets at once, so this method chooses the correct fetch routine and yields record batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream’s name, calls the matching private fetch method, and yields each batch that method produces. If BambooHR rejects the request with 401 or 403, it converts that into a `StreamSkipped` message explaining that the API key or permissions are not sufficient.

**Call relations**: The sync engine calls `paginate` whenever it wants records for one BambooHR stream. `paginate` then delegates to `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports` depending on the stream name.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR’s employee directory. The directory is a broad list of employees and is one of the core streams the connector exposes.

**Data flow**: It uses the HTTP client to GET `/v1/employees/directory`. From the returned JSON object, it looks under the `employees` key. If that list contains records, it yields the whole list as one batch; if it is empty, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. It relies on the connector’s lower-level GET helper from the parent `RestConnector` to perform the actual HTTP request.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads full per-employee detail records. BambooHR does not provide these as one simple list, so the connector first gets the directory and then fetches each employee one by one.

**Data flow**: It starts by GETting `/v1/employees/directory` and reading the `employees` list. For each directory row that has an `id`, it GETs `/v1/employees/{id}`. If the detail response is a dictionary-like record, it makes sure the record has the same `id`, then yields that single employee detail as a one-item batch.

**Call relations**: `paginate` calls this for the `employees` stream. This method creates a fan-out flow: one directory response becomes many individual employee detail requests, giving the sync engine small checkpoints as each employee is returned.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads time-off requests from BambooHR. It uses a date window so repeat syncs can start from the last saved point instead of always asking from the beginning.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into `start` and `end` request parameters, then GETs `/v1/time_off/requests/`. BambooHR may return either a plain list or an object containing `requests`; the method accepts both shapes and yields the records if any are present.

**Call relations**: `paginate` calls this for the `time_off_requests` stream. Before making the request, it asks `_date_window_params` to build the date filter BambooHR requires.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads timesheet entry records from BambooHR. Like time-off requests, it asks BambooHR for entries inside a date window based on the sync cursor.

**Data flow**: It receives the HTTP client and an optional cursor. It converts the cursor into `start` and `end` parameters, then GETs `/v1/time_tracking/timesheet_entries`. If BambooHR returns a list, it uses it directly; if BambooHR returns an object, it reads the `entries` list. It yields the records only when there is something to send onward.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. It shares the `_date_window_params` helper with `_fetch_time_off` because both BambooHR endpoints require the same kind of date range.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR’s field catalog, which describes the available employee fields. This helps the sync know what kinds of fields BambooHR exposes.

**Data flow**: It GETs `/v1/meta/fields` through the HTTP client. If BambooHR returns a list, it uses that list; otherwise, it looks for a `fields` list inside the returned object. If records exist, it yields them as one batch.

**Call relations**: `paginate` calls this when syncing the `meta_fields` stream. It is a simple one-request fetch compared with the employee detail flow.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method asks BambooHR to generate a custom report containing a chosen set of employee fields. It is useful when the connector wants a tailored employee-shaped export rather than BambooHR’s default endpoint output.

**Data flow**: It builds a JSON request body with a report title and a fixed list of fields such as employee ID, name, email, job title, department, supervisor, hire date, and status. It POSTs that body to `/v1/reports/custom`, then reads the returned `employees` list. If records are present, it yields them as one batch.

**Call relations**: `paginate` calls this for the `custom_reports` stream. Unlike most other fetch helpers in this file, it uses a POST request because BambooHR’s custom report endpoint expects the requested fields in the request body.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the date range parameters required by BambooHR’s time-off and timesheet endpoints. It turns the sync cursor into a simple start date.

**Data flow**: It receives an optional cursor, which may be an ISO-style timestamp. If a cursor is present and not blank, it takes the first 10 characters, matching the `YYYY-MM-DD` date format BambooHR expects. If no cursor is present, it starts at `1970-01-01` so a first sync can fetch everything. It always returns an end date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this just before making their API requests. It keeps both methods consistent, so they use the same cursor-to-date-window rule.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/providers/deel.py`

`io_transport` · `source sync / data fetching`

Deel is an HR platform, and its API returns information in pages rather than all at once. This file is the connector that knows which Deel objects to read, where to find them, and how to keep asking for the next page until there is no more data. Without it, the system would not know how to pull Deel records into its searchable or recallable pages.

The file first defines the available streams: contracts, forms, payslips, timesheets, and tasks. A stream is simply one kind of object the sync can fetch. Most streams use an `updated_at` field as a cursor, meaning the connector can ask Deel for “only things changed after this time.” Forms do not have that cursor, so they are fetched in full each run.

The `DeelConnector` then provides the concrete reading behavior. It builds request parameters, calls Deel’s REST API using `limit` and `offset` pagination, extracts the actual records from Deel’s response, and yields them page by page. Think of it like reading a long book by asking for 100 pages at a time until the last batch is shorter than expected, which means the book has ended.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a `StreamSpec`, which is the small description the sync system uses to know what kind of Deel object to fetch. It fills in common defaults so each stream can be declared in one short line.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, and whether the stream is canonical. It uses those values to create and return a `StreamSpec` object, with sensible defaults like `id` for the primary key and `updated_at` for incremental syncing.

**Call relations**: This helper calls `StreamSpec.__init__` to build the stream description. The file uses these stream descriptions to define the Deel stream list that the connector later exposes to the wider source-sync system.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the starting query parameters for a Deel API request. It always asks for up to 100 records, and when possible it adds a time filter so the sync only fetches records updated after the saved cursor.

**Data flow**: It receives a stream description and an optional cursor timestamp. It starts with `limit: 100`; if there is a cursor and the stream supports a cursor field, it adds `updated_after` with that timestamp. It returns the completed parameter dictionary used for API calls.

**Call relations**: `DeelConnector.paginate` calls this before making requests. The result becomes the base set of parameters that each paged API request reuses, with `paginate` adding the changing `offset` value for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: Pulls usable record dictionaries out of a Deel API response. It accepts Deel’s usual `{data: [...]}` shape, and also tolerates a plain list response.

**Data flow**: It receives raw response data from the API. If the data is a dictionary with a `data` list, it keeps only items in that list that are themselves dictionaries. If the response is already a list, it does the same filtering. If the response is in some other shape, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each API request. Its output decides whether there are records to yield to the sync system, and whether pagination should continue or stop.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Deel stream from the API, page by page. It is the main reading loop that turns Deel’s paginated HTTP responses into batches of records for the rest of the sync pipeline.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It builds the API path, gets the base query parameters, then repeatedly adds an `offset`, sends a GET request, extracts records, and yields each non-empty batch. It stops when Deel returns no records or fewer than the page size, which signals there are no more pages.

**Call relations**: This method is the point where the connector’s pieces come together. It calls `_initial_params` to prepare the request, uses the inherited `_get` method to fetch data from Deel, then calls `_extract_records` to turn the response into usable batches before handing those batches back to the source-sync machinery.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### Hiring pipeline ingestion
Syncs candidates, jobs, applications, interviews, offers, departments, and related recruiting records from applicant tracking systems.

### `extensions/sources/ufo_ext_sources/providers/greenhouse.py`

`io_transport` · `during Greenhouse source sync`

Greenhouse is an external recruiting system, and its Harvest API exposes hiring data through many separate web addresses. This file is the connector for that API. Without it, the project would not know which Greenhouse endpoints exist, how to log in, how to move through paginated results, or how to fetch child records such as a job’s openings or a candidate’s activity feed.

The file first defines a catalog of streams. A stream is one kind of data the system can read, like “candidates” or “job stages.” Most streams map directly to one Greenhouse endpoint. Some are nested: for example, to read job openings, the connector must first read jobs, then ask Greenhouse for the openings under each job. The connector stamps those child records with the parent id, like putting a return address on a package, so later code can tell where each child record came from.

Authentication is also adjusted here. Greenhouse uses HTTP Basic authentication, which means the API key is sent as the username and the password is blank. The connector only does that when it has the key locally; if an auth broker is being used, the broker adds authentication instead.

Pagination follows Greenhouse’s `Link` header, a response header that says where the next page is. Some streams can also be read incrementally by asking only for records updated after a saved cursor time. If Greenhouse refuses access to a stream, the connector marks that stream as skipped rather than failing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream description in a compact, consistent way. It is used to define each Greenhouse data type the connector can read, including its name, id field, time fields, and whether it is one of the main canonical streams.

**Data flow**: It takes basic facts about one stream, such as its public name, the Greenhouse object path name, its primary key, and optional timestamp fields. It fills in sensible defaults when details are not supplied, then produces a StreamSpec object that the sync system can use later to know how that stream should be read and tracked.

**Call relations**: At import time, the file calls this helper many times to build the connector’s stream catalog. The resulting StreamSpec objects are gathered into ALL_STREAMS, which GreenhouseConnector exposes as its list of readable streams.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client that will talk to Greenhouse. Its special job is to use Greenhouse’s required login style: the API key as the Basic Auth username with an empty password.

**Data flow**: It starts with the normal client built by the parent RestConnector. If the resolved credential contains a bearer value, this function treats that value as the Greenhouse API key, sets Basic Auth on the client, and removes any ordinary Authorization header that would not be correct for Greenhouse. If the credential does not expose the key locally, the client is left as the base connector made it, so an auth proxy can add authentication elsewhere.

**Call relations**: The wider connector setup calls this when it needs a web client for a Greenhouse sync. It builds on the parent connector’s client creation, then hands back a Greenhouse-ready client for pagination and stream reads.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This small helper chooses the correct query parameter name for incremental reads. Most Greenhouse streams use `updated_after`, but a few use different names, and this function keeps that exception list in one place.

**Data flow**: It receives a stream name. It looks up whether that stream has a special cursor parameter, such as `created_after` for applications or `submitted_after` for EEOC records. If not, it returns the default `updated_after` name.

**Call relations**: GreenhouseConnector.paginate calls this when it is about to request an incremental stream with a saved cursor. The returned parameter name is placed into the request sent to Greenhouse.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading routine for a Greenhouse stream. It decides which Greenhouse endpoint to call, adds pagination and cursor options, and yields each page of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. If the stream is a nested child stream, it delegates to the per-parent reader. Otherwise, it finds the simple endpoint path, adds `per_page=500`, and, when possible, adds a cursor filter so Greenhouse returns only newer records. It then yields lists of records page by page. If Greenhouse replies with 401 or 403, meaning unauthorized or forbidden, it converts that into a StreamSkipped signal so the run records a skipped stream instead of a total failure.

**Call relations**: The source sync process calls this for each stream it wants to read. Inside, it uses _cursor_param for incremental filters, _paginate_link_header for normal endpoint paging, and _paginate_per_parent for child streams that must be read under each parent record.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a Greenhouse endpoint one page at a time by following the API’s `Link` header. A `Link` header is like a “next page” sign included in the web response.

**Data flow**: It receives the HTTP client, an endpoint path, and optional query parameters. It asks the shared REST machinery to fetch pages using Greenhouse’s page size of 500 records and yields each page as a list of record dictionaries. It does not reshape the records; it passes through Greenhouse’s flat JSON arrays.

**Call relations**: GreenhouseConnector.paginate uses this for ordinary top-level streams. GreenhouseConnector._paginate_per_parent also uses it twice: first to page through parent records, then to page through each parent’s child records.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads nested Greenhouse data that only exists under a parent record, such as openings under a job or permissions under a user. It keeps child records tied back to their parent by adding the parent id to each child record.

**Data flow**: It receives a parent endpoint path, a child endpoint template containing `{id}`, and the field name where the parent id should be stored. It pages through the parent records, takes each parent’s id, fetches that parent’s child pages, and adds the parent id to each child dictionary if the child does not already have it. It yields each child page after stamping it.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is listed as a per-parent stream. This function relies on _paginate_link_header for both parent and child paging, so all nested reads use the same Greenhouse pagination behavior as ordinary streams.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/recruitee.py`

`io_transport` · `source sync`

Recruitee is an online hiring tool, and this connector is the bridge between Recruitee and this project’s source-sync system. Its job is read-only: it asks Recruitee for lists of records and yields them in batches so they can be stored or indexed elsewhere.

The file defines the Recruitee streams the system knows about: candidates, offers, and departments. A “stream” here means one kind of list to fetch from the outside service. Each stream says which Recruitee object it maps to and which field uniquely identifies each record.

Recruitee’s API returns data in numbered pages, like flipping through search results: page 1, page 2, and so on. This connector uses a fixed page size of 100 and keeps asking for more pages until the shared REST helper decides there are no more full pages to fetch. The connector does not keep an API token itself; authentication is supplied by the surrounding runner.

One important safety choice is that the default base URL is empty. Recruitee URLs include a company-specific tenant ID, so the sync must provide the correct full API prefix. If it does not, the connector should fail clearly instead of accidentally calling the wrong place.

If Recruitee responds with “unauthorized” or “forbidden,” the connector skips that stream with a clear explanation rather than treating it as normal missing data.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream, such as candidates or offers, one page at a time. It is used during a sync to turn Recruitee’s paginated API response into batches of records the rest of the system can consume.

**Data flow**: It receives an HTTP client, a stream description, and an unused cursor value. From the stream description, it builds the Recruitee path, such as `/candidates`, then asks the shared REST pagination helper for pages of up to 100 records. Each page of records comes out as a list of dictionaries. If Recruitee refuses access with a 401 or 403 response, it turns that into a clear `StreamSkipped` result instead of yielding records.

**Call relations**: During a source sync, the broader connector framework calls this method when it needs records for a Recruitee stream. The method relies on the inherited page-number fetching behavior to do the repeated HTTP requests. If access is refused, it creates a `StreamSkipped` exception so the sync runner can understand that this particular stream could not be read because of missing permission or bad credentials.

*Call graph*: calls 1 internal fn (__init__).


### Workforce organization ingestion
Reads company, worker, and team metadata from Rippling for workforce synchronization.

### `extensions/sources/ufo_ext_sources/providers/rippling.py`

`io_transport` · `source sync request handling`

Rippling is an external HR and company-management service. This connector is the adapter that lets the project pull data from Rippling without the rest of the system needing to know Rippling’s exact web API shape. It defines three readable streams: companies, workers, and teams. Workers and teams can be synced incrementally, meaning the connector can ask for only records updated after a saved timestamp. Companies do not have that cursor, so they are read as a full refresh.

The main job is pagination. Rippling sends list results in pages, like a book split into chapters. Each response may include records under a resource-specific name such as `workers`, or under a generic `data` field, and may include a `next` link pointing to the following page. The connector starts at the stream’s first endpoint, asks for up to 100 rows, yields any valid record dictionaries it finds, then follows `next` until there are no more pages.

Authentication is not stored here. The wider source runner supplies an HTTP client that already knows how to authenticate. If Rippling rejects the request with a 401 or 403 status, the connector raises `StreamSkipped`, which tells the sync runner that this stream cannot be read because the key is invalid or lacks permission.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s `next` pagination link into the path the HTTP client should request next. It accepts both full web addresses and shorter relative paths.

**Data flow**: It receives a `next` link, which may be missing, absolute, or relative. If it is missing, it returns nothing. If it is a full URL, it strips it down to just the path and query string, because the connector already knows Rippling’s base address. If it is already a relative path, it returns it unchanged.

**Call relations**: During pagination, `RipplingConnector.paginate` asks this helper what page to request after each response. This keeps the main loop simple: read a page, extract records, then ask `_next_path` where to go next.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first request to a Rippling stream. It sets the page size and, when possible, adds the timestamp used for incremental syncing.

**Data flow**: It receives the stream description and an optional saved cursor value. It always starts with a `limit` of 100. If the stream has a cursor field and a cursor value was provided, it adds `updatedAfter` so Rippling returns only newer updates. It returns the completed parameter dictionary.

**Call relations**: At the start of `RipplingConnector.paginate`, this function prepares the first request. After that first page, the pagination loop follows Rippling’s own `next` links instead of rebuilding the original query.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls actual record objects out of Rippling’s response body, even though Rippling may wrap them in different ways. It filters out anything that is not a dictionary-like record.

**Data flow**: It receives decoded response data and the stream being read. If the data is a dictionary, it first looks for a list under the stream name, such as `workers`, then looks for a generic `data` list. If the whole response is already a list, it uses that. In all cases, it returns only items that are record dictionaries; otherwise it returns an empty list.

**Call relations**: `RipplingConnector.paginate` calls this after each HTTP response. The helper gives the pagination loop clean batches of records to yield, so the rest of the sync system does not need to care about Rippling’s response envelope.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main reading loop. It requests each page for a Rippling stream, yields batches of records, follows the `next` link, and turns permission failures into a clear skipped-stream signal.

**Data flow**: It receives an authenticated async HTTP client, a stream description, and an optional cursor timestamp. It builds the first endpoint path and query parameters, repeatedly fetches a page, extracts valid records, yields them if any exist, and then moves to the next page if Rippling provided one. If Rippling replies with 401 or 403, it raises `StreamSkipped`; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: This method is called by the broader source-sync runner when it wants records from one Rippling stream. Inside the loop it relies on `_initial_query` to prepare the first request, `_extract_records` to find rows in each response, and `_next_path` to continue through pagination. When access is refused, it hands the runner a `StreamSkipped` exception so the problem is reported as a permission or credential issue rather than as a broken parser.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

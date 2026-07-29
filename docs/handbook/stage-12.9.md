# HR and recruiting source connectors  `stage-12.9`

This stage is the system’s set of “adapters” for HR and recruiting tools. It is used during the main sync work, when the system reaches out to outside services and copies their records into a common form it can store, search, or use later. Each connector knows the rules of one service’s web API, meaning the doorway the service provides for software to request data.

Ashby and Greenhouse focus on recruiting pipelines, pulling candidates, jobs, applications, interviews, offers, users, and lookup lists. Recruitee covers similar hiring data, such as candidates, job offers, and departments. BambooHR reads broader employee and HR datasets from BambooHR. Deel brings in payroll-adjacent workforce records like contracts, forms, payslips, timesheets, and tasks. Rippling reads companies, workers, and teams.

Together, these files act like translators at different service desks. Each one asks its service for data page by page, turns the replies into steady streams of records, and hands them to the wider sync system in a consistent shape.

## Files in this stage

### Ashby recruiting connector
Introduces the Ashby connector for syncing recruiting entities such as candidates, jobs, applications, interviews, offers, users, and lookup lists.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `during source sync, when Ashby records are being fetched`

Ashby is a recruiting system, and its API sends data back in pages rather than all at once. This file is the adapter that knows Ashby's rules: every list request is a POST request, each request asks for up to 100 records, and Ashby says whether there is another page to fetch. Think of it like reading a long book one chapter at a time, using a bookmark to know where to continue.

The file first defines the list of Ashby streams the system can read. A stream is one kind of object, such as candidates or jobs. Each stream records the Ashby endpoint to call, the field that uniquely identifies a record, and, when available, the timestamp field used for incremental syncing. Incremental syncing means reading only records changed since the last run instead of rereading everything.

The `AshbyConnector` then provides the network behavior. It builds an authenticated HTTP client using Ashby's required Basic authentication format, where the API key is used as the username. For normal streams, it repeatedly calls the right Ashby endpoint until there are no more pages. One stream is special: criteria evaluations belong under individual applications, so the connector first lists applications, then asks Ashby for evaluations for each application id. This file is read-only; it contains no logic for writing data back to Ashby.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Ashby object type. It keeps the stream catalog compact and consistent by filling in common fields such as created and updated timestamps.

**Data flow**: It receives a friendly stream name, an Ashby API path, and optional details like the unique id field and update-time field. It packages those details into a `StreamSpec`, which is the system's standard description of how to read one kind of source record. The result is used later by the connector when deciding which endpoint to call and how to track changes.

**Call relations**: This helper is used while building the `ASHBY_STREAMS` list at import time. It hands stream descriptions to the rest of the connector, and internally it creates `StreamSpec` objects so the shared source framework can understand Ashby's catalog.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method builds the HTTP client used to talk to Ashby, with authentication in the exact form Ashby expects. It also respects a preconfigured proxy transport when credentials are supplied that way.

**Data flow**: It receives the API base URL and a `Credential`, which may contain either a direct API key or a special transport supplied by an authentication broker. If a transport is already present, it lets the parent connector build the client unchanged. Otherwise it takes the API key, encodes it as an HTTP Basic Authorization header, and returns a client configured with that header. If there is no usable API key, it raises an error instead of making unauthenticated requests.

**Call relations**: The shared REST connector framework calls this when it needs a network client for Ashby. This method prepares the Ashby-specific authentication, then hands the actual client creation back to the parent `RestConnector` implementation.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method chooses how to fetch pages for a given Ashby stream. Most streams use the normal Ashby list paging pattern, but criteria evaluations need a special per-application lookup.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It checks the stream name. For `application_criteria_evaluations`, it yields pages produced by the special application fan-out method. For every other stream, it yields pages from the standard paging method, passing along the cursor so Ashby can return only changed records when possible.

**Call relations**: The source framework calls this when it wants records from a stream. This method acts like a traffic director: it sends ordinary streams to `AshbyConnector._paginate_default` and sends the special criteria-evaluation stream to `AshbyConnector._paginate_application_criteria`.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads a normal Ashby list endpoint page by page. It supports incremental syncing by sending the previous run's cursor as Ashby's `syncToken`.

**Data flow**: It starts with a request body asking for 100 records. If a previous cursor exists, it adds that as `syncToken`, which tells Ashby to return records changed since that point. It then repeatedly posts to the stream's Ashby endpoint, yields any returned records, and follows Ashby's `nextCursor` while `moreDataAvailable` is true. It stops when Ashby says there is no more data or when no next cursor is provided.

**Call relations**: This is called by `AshbyConnector.paginate` for all ordinary streams, such as candidates, jobs, applications, users, and lookup lists. It relies on the connector's `_post` helper from the shared REST machinery to make each network request, then returns record batches to the caller as they arrive.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches criteria evaluations, which Ashby exposes only by application id rather than as one simple global list. It first finds applications, then asks for the evaluations attached to each one.

**Data flow**: It pages through `/application.list` in batches of 100 applications. For each application record that has an id, it posts that id to `/application.listCriteriaEvaluations`. It copies each returned evaluation into a new row and makes sure the row includes the `applicationId`, so the evaluation can still be tied back to its application later. It yields non-empty batches of stamped evaluation rows, then continues until the application list has no more pages.

**Call relations**: This is called only by `AshbyConnector.paginate` when the requested stream is `application_criteria_evaluations`. It uses the same Ashby POST helper as the default paginator, but it performs a two-step walk: list parent applications first, then fetch child evaluation records for each application.

*Call graph*: called by 1 (paginate).


### HR operations connectors
Covers BambooHR and Deel connectors for syncing employee, contractor, payroll-adjacent, forms, timesheet, task, and other HR operations data.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `during source sync, when BambooHR streams are read`

BambooHR exposes employee and HR data through web endpoints, but the endpoints do not all behave the same way. Some return a list inside an "employees" field, some return a plain list, some need a date range, and the custom report endpoint must be called with a POST request instead of a normal GET request. This file gathers those differences in one connector so the rest of the sync system can treat BambooHR like a set of named streams.

The file first defines the streams the connector can read, such as the employee directory, detailed employee records, time-off requests, timesheet entries, metadata fields, and a custom employee report. A stream is like a labeled shelf of records that the sync engine can ask for.

The `BambooHRConnector` then builds an HTTP client with the right BambooHR authentication. BambooHR expects HTTP Basic authentication, with the API key as the username and the literal password `"x"`. It also explicitly asks BambooHR for JSON, because BambooHR otherwise may return XML.

When the sync engine asks for a stream, `paginate` routes the request to the right helper. BambooHR does not use normal page-by-page pagination here, so each helper yields one or more batches of records in the shape the sync engine expects. If BambooHR rejects access with a permission or authentication error, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: Creates a `StreamSpec`, which is the small description object the sync system uses to know what a BambooHR stream is called and which fields identify or order its records.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a `StreamSpec` object that the connector later exposes as part of its stream list.

**Call relations**: This helper is used while the file is loaded to build `BAMBOOHR_STREAMS`. It hands the finished stream descriptions to the connector class, so later `paginate` can decide which BambooHR endpoint to call for each named stream.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the web client used to talk to BambooHR. It sets time limits, JSON headers, the tenant-specific base URL, and the correct authentication method.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares headers that ask for JSON, and creates an `httpx.AsyncClient`. If the credential includes a custom transport, it uses that unchanged; otherwise it turns the API key into BambooHR’s required Basic authentication. If no usable authentication is present, it raises an error.

**Call relations**: The broader REST connector framework calls this when it is preparing to sync BambooHR. The client it returns is then passed into `paginate` and the fetch helpers, which use it to make the actual API calls.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right BambooHR fetching method for the requested stream and yields batches of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching private fetch method, and passes through each batch that method yields. If BambooHR returns a 401 or 403 refusal, it turns that into a `StreamSkipped` message explaining that the key or permission scope is not enough.

**Call relations**: This is the main dispatch point used by the sync framework after setup. It sends employee-directory requests to `_fetch_directory`, detailed employee requests to `_fetch_employees`, time-based streams to `_fetch_time_off` or `_fetch_timesheets`, metadata requests to `_fetch_meta_fields`, and custom report requests to `_fetch_custom_reports`.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches BambooHR’s employee directory, which is the basic list of employees.

**Data flow**: It sends a request to the employee directory endpoint. From the response, it takes the list found under `employees`. If the list is not empty, it yields that list as one batch of records.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. This helper is the simplest read path: one BambooHR request becomes one sync batch.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches detailed employee records one employee at a time. It first uses the directory as an index, then asks BambooHR for each employee’s full detail record.

**Data flow**: It starts by requesting the employee directory. For each valid directory row with an `id`, it requests `/v1/employees/{id}`. If BambooHR returns a detail object, the function makes sure it contains the employee id and yields that single employee as its own batch.

**Call relations**: `paginate` calls this for the `employees` stream. It depends on the directory endpoint to discover which employee detail URLs to visit, like using a phone book before calling each person individually.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches time-off requests within a date window. This is used so a sync can start from the last known point instead of always asking for the same range by hand.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into BambooHR `start` and `end` parameters using `_date_window_params`, requests the time-off endpoint, then accepts either a plain list response or a response with records under `requests`. If records exist, it yields them as one batch.

**Call relations**: `paginate` calls this when syncing `time_off_requests`. Before making the API request, it asks `_date_window_params` to build the date range BambooHR requires.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches timesheet entry records within a date window, using the last sync cursor when available.

**Data flow**: It receives the HTTP client and optional cursor. It converts the cursor into `start` and `end` query parameters, calls BambooHR’s timesheet entries endpoint, and reads records either from a plain list response or from an `entries` field. If records are present, it yields them as one batch.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. Like the time-off fetcher, it relies on `_date_window_params` so the endpoint always gets the date range BambooHR expects.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches BambooHR’s field catalog, which describes the available employee fields and other metadata.

**Data flow**: It requests the metadata fields endpoint. It accepts either a plain list response or a response with records under `fields`. If the resulting list has records, it yields them as one batch.

**Call relations**: `paginate` calls this when the sync engine asks for `meta_fields`. This gives the system a way to read BambooHR’s own description of its data fields.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs a predefined BambooHR custom report and returns the employee rows from it. This gives the sync a controlled set of useful employee columns in one response.

**Data flow**: It builds a report request body with a title and a chosen list of fields, such as name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to BambooHR’s custom report endpoint, then takes the returned rows from `employees` and yields them if any exist.

**Call relations**: `paginate` calls this for the `custom_reports` stream. Unlike most other helpers, it posts a request body first because BambooHR needs to know which report fields to include before it can return the rows.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: Turns an optional sync cursor into the `start` and `end` dates BambooHR requires for time-based endpoints.

**Data flow**: It receives a cursor value, usually a saved timestamp from an earlier sync. If the cursor is present and not blank, it takes the first ten characters as a `YYYY-MM-DD` date. If there is no cursor, it starts at `1970-01-01` so a fresh sync can collect everything. It always returns a dictionary with that start date and a far-future end date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this before contacting BambooHR. It keeps their date-window behavior consistent, so both endpoints get the same kind of parameters.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `during source sync when Deel records are fetched`

This connector is the bridge between Deel and the larger UFO source system. Deel exposes its data through a REST API, which means the system must ask a web address for records, page by page, instead of reading everything at once. Without this file, the project would not know which Deel objects to fetch, how to ask for the next page, or how to do smaller “only what changed” syncs when Deel supports them.

The file first defines the list of Deel streams. A stream is one kind of data to copy, such as contracts or payslips. Most streams use an `updated_at` cursor, which is a timestamp marker meaning “only send records changed after this time.” Forms do not have that marker, so they are fetched fully each run.

`DeelConnector` then provides the Deel-specific rules. It points to Deel’s API base address, builds query parameters like page size and update cutoff, extracts records from Deel’s response shape, and loops through pages using an `offset`. Think of it like reading a long report 100 rows at a time: after each batch, it moves the bookmark forward until there are no more rows or the final batch is shorter than expected. This file only reads from Deel; it does not write changes back.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Deel data stream, such as `contracts` or `tasks`. This description tells the shared source framework what the stream is called, what field identifies each record, and whether it can be synced incrementally.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the shared record describing how that stream should be synced.

**Call relations**: This helper is used while the file is loaded to build the `DEEL_STREAMS` list. It hands each completed stream description to the connector class through `streams_list`, so later sync code knows which Deel endpoints to read.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the starting web request parameters for a Deel stream. It sets the page size and, when possible, adds the timestamp that asks Deel for only records updated after the last sync point.

**Data flow**: It takes a stream description and an optional cursor value. It always creates a parameter dictionary with `limit` set to 100, and if both a cursor and cursor field exist, it adds `updated_after` with that cursor value. The result is a dictionary ready to be sent with the API request.

**Call relations**: `DeelConnector.paginate` calls this before it starts fetching pages. The returned parameters become the base request options that are reused for every page, with only the page `offset` changing as pagination advances.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: Pulls usable record objects out of Deel’s API response. It protects the sync from unexpected response shapes by returning only dictionary-like records and ignoring anything else.

**Data flow**: It receives raw response data from Deel. If the data is a dictionary with a `data` list inside, it keeps only the list items that are dictionaries. If the response itself is already a list, it does the same filtering there. If neither shape matches, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each API request. The extracted records are what get yielded onward to the shared sync machinery; an empty result tells pagination that there is nothing more useful to read.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Deel stream from the API in pages and yields each batch of records. It is the main read loop for Deel data.

**Data flow**: It receives an async HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Deel API path, creates base request parameters, then repeatedly adds an `offset`, sends a GET request, extracts records, and yields each non-empty batch. It stops when Deel returns no records or a batch smaller than the page size, which means the end has been reached.

**Call relations**: The shared `RestConnector` machinery calls this when it needs records for a Deel stream. Inside the loop, it relies on `_initial_params` to form the request and `_extract_records` to normalize the response before handing batches back to the rest of the sync process.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### Hiring pipeline connectors
Covers Greenhouse and Recruitee connectors for syncing hiring pipeline records including candidates, jobs, applications, interviews, offers, departments, and related recruiting data.

### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `during source sync`

Greenhouse stores recruiting information behind many web API endpoints. This file is the adapter that knows which Greenhouse URL to call for each kind of data, how to authenticate, how to move through paginated results, and how to skip streams the current API key is not allowed to read.

Most Greenhouse endpoints return a plain list of records, so the connector can pass each page through without unpacking a wrapper object. For simple streams, such as candidates or jobs, it calls one top-level path and follows Greenhouse’s “next page” link until there are no more pages. For nested streams, such as a job’s openings or a candidate’s activity feed, it first reads the parent records, then asks Greenhouse for each parent’s child records. It also stamps each child with the parent id, like putting a label on a folder so later readers know where it came from.

The connector supports incremental syncing for streams that have a date field, meaning it can ask Greenhouse for only records changed after a saved point in time. If Greenhouse replies with “unauthorized” or “forbidden,” the stream is marked as skipped instead of failing the whole run. This matters because Greenhouse API keys often have partial access.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: Builds a stream description for one Greenhouse data set. A stream description tells the sync system the stream’s name, where the data comes from, its record id field, and which date fields can be used for incremental updates.

**Data flow**: It receives a stream name and optional details such as the source object path, primary key, cursor field, and date fields. It fills in sensible defaults where details are missing, then creates and returns a StreamSpec object that the connector later uses as its recipe for that stream.

**Call relations**: This helper is used while the module is being loaded to define all of the Greenhouse streams in one consistent style. Its main handoff is to StreamSpec.__init__, which stores the recipe in the common format expected by the rest of the source syncing system.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Greenhouse, with the right authentication style. Greenhouse Harvest expects HTTP Basic authentication, where the API key is sent as the username and the password is blank.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent RestConnector to build the normal client. If the credential contains a bearer value directly on the host, it replaces the usual authorization header with HTTP Basic authentication using that value as the username. It returns the prepared client.

**Call relations**: This is part of the connector setup before API calls are made. It relies on the base connector for the standard client, then uses httpx.BasicAuth when Greenhouse needs the key to be sent directly. If authentication is being injected by a broker or proxy, it leaves the base client’s setup alone.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: Chooses the query parameter name Greenhouse expects for incremental syncing on a given stream. Most streams use updated_after, but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It checks a small lookup table for special cases, such as applications or EEOC records, and otherwise returns the default parameter name updated_after.

**Call relations**: GreenhouseConnector.paginate calls this when it is about to request only records after a saved cursor value. This keeps the main pagination code simple while still matching Greenhouse’s endpoint-by-endpoint naming differences.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Decides how to fetch pages for a requested Greenhouse stream. It knows whether a stream is a simple top-level endpoint or a child collection that must be fetched once per parent record.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. If the stream is nested under parent records, it sends the work to _paginate_per_parent. Otherwise it looks up the Greenhouse path, adds page size and cursor parameters when needed, and yields each page from _paginate_link_header. If Greenhouse says the key is unauthorized or forbidden, it turns that into a StreamSkipped result instead of letting the whole sync crash.

**Call relations**: This is the main paging entry for this connector during a sync. It calls _cursor_param to name incremental filters correctly, _paginate_link_header for ordinary endpoints, and _paginate_per_parent for nested endpoints. When access is refused, it raises StreamSkipped so the broader sync runner can record that the stream was skipped.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through a Greenhouse endpoint one page at a time using the API’s “next page” link. This is the common paging method for top-level endpoints and for each nested child endpoint.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST helper to request pages with the configured page size, follows Greenhouse’s Link header when there is another page, and yields each list of records as it arrives.

**Call relations**: GreenhouseConnector.paginate calls this for simple streams. GreenhouseConnector._paginate_per_parent also calls it twice: first to read parent records, then to read each parent’s child records. It delegates the low-level link-following work to the shared REST connector behavior.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches nested Greenhouse records, such as openings for each job or activity feed entries for each candidate. It preserves the connection between each child record and the parent record it came from.

**Data flow**: It receives the parent endpoint path, a child endpoint template, and the name of the field where the parent id should be stored. It pages through all parents, takes each parent id, formats the child URL for that id, pages through the child records, adds the parent id to each child record when possible, and yields the child pages.

**Call relations**: GreenhouseConnector.paginate calls this for streams listed as per-parent streams. This function uses _paginate_link_header for both the parent and child API calls, so all pagination still follows the same Greenhouse Link-header pattern.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `during source sync when Recruitee streams are being fetched`

This connector is the bridge between UFO and Recruitee, a recruiting platform. Without it, the system would not know which Recruitee lists exist, where to ask for them, or how to walk through many pages of results safely.

The file defines three Recruitee streams: candidates, offers, and departments. A stream is one kind of data the sync can fetch. Candidates and offers are marked as the main, or canonical, data; departments are included too, but not treated as a primary content source in the same way.

Recruitee’s API returns list data in numbered pages, like turning pages in a catalog: ask for page 1, then page 2, and keep going until the page is shorter than the maximum size. This connector uses that pattern with 100 records per page. It expects the full tenant-specific base URL to be supplied elsewhere, because each Recruitee company has its own API prefix. That avoids accidentally calling a wrong or generic host.

The connector only reads data. It does not create or update anything in Recruitee. If Recruitee answers with “unauthorized” or “forbidden,” the connector marks that stream as skipped with a clear message, rather than crashing the whole sync without explanation.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream, such as candidates or offers, page by page. It is used when the sync runner needs all records from that stream and must keep asking Recruitee for the next numbered page until there is no more data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. The cursor is not used here because these Recruitee streams are full refreshes, meaning the connector reads the whole list each time. It builds the API path from the stream name, asks the shared REST helper for numbered pages of up to 100 records, and yields each page as a list of record dictionaries. If Recruitee rejects the request with a 401 or 403 response, it turns that into a StreamSkipped error with a human-readable reason; other HTTP errors are passed upward unchanged.

**Call relations**: The sync framework calls this method when it is time to read a Recruitee stream. The method delegates the repetitive page-fetching work to the base REST connector’s page-number helper, then hands each page back to the caller as it arrives. If access is refused, it creates a StreamSkipped exception so the larger sync can understand that this stream could not be read because of missing permission or a bad key.

*Call graph*: calls 1 internal fn (__init__).


### Workforce directory connector
Finishes with the Rippling connector for syncing company, worker, and team directory records from Rippling's paged REST API.

### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `during source sync when reading Rippling streams`

Rippling is an HR platform, and this connector is the system's doorway into its company, worker, and team data. The file says which Rippling resources can be read, how each one is identified, and whether it can be synced incrementally. Incremental syncing means asking only for records changed after a saved time, instead of downloading everything again.

The connector is intentionally read-only. It does not create or update anything in Rippling. It also does not store the API token itself; authentication is supplied by the wider runner through the HTTP client.

The main job here is pagination. Rippling returns list results in pages, like a long document split across several screens. Each response may include a `next` link pointing to the following page. The connector starts at the resource path, asks for up to 100 records, yields any valid records it finds, then follows `next` until there is no next page.

Rippling may wrap records in slightly different shapes: under a key named after the stream, under a generic `data` key, or directly as a list. This file accepts those shapes but only keeps dictionary-like records. If Rippling refuses access with a 401 or 403 response, the connector reports that the stream was skipped, usually because the token is invalid or lacks permission.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling's `next` page link into a path the connector can request next. It accepts both full web addresses and already-relative paths, because APIs often return either form.

**Data flow**: It receives a `next` link, or nothing. If there is no link, it returns nothing, meaning pagination is finished. If the link is a full URL, it strips it down to just the path and query string; if it is already a path, it returns it as-is.

**Call relations**: During pagination, `RipplingConnector.paginate` calls this after each response to decide where to go next. This helper uses URL parsing so the rest of the loop can work with a simple request path rather than worrying about the different link formats Rippling might send.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the first set of query parameters for a Rippling list request. It sets the page size and, when possible, adds the saved cursor so only recently changed records are requested.

**Data flow**: It receives the stream definition and an optional cursor value, such as the last `updatedAt` time seen in a previous sync. It always creates a request asking for 100 records. If the stream supports a cursor and a cursor value exists, it adds `updatedAfter` so Rippling filters the results. The finished parameter dictionary is returned.

**Call relations**: At the start of `RipplingConnector.paginate`, this function prepares the parameters for the first API call. After that first request, pagination follows Rippling's `next` links, so the main loop clears the parameters and lets the next link carry the needed page information.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls actual record objects out of the response body returned by Rippling. It exists because Rippling list responses can be wrapped in more than one acceptable shape.

**Data flow**: It receives the decoded response data and the stream being read. If the data is a dictionary, it first looks for a list under the stream name, such as `workers` or `teams`; then it looks for a generic `data` list. If the response itself is a list, it uses that. In all cases it keeps only dictionary records and returns them as a list; anything unrecognized becomes an empty list.

**Call relations**: `RipplingConnector.paginate` calls this after each API response. The extracted records are what get yielded to the rest of the sync system, while malformed or unexpected non-record items are quietly ignored.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It requests one page at a time, yields batches of records, and follows Rippling's `next` links until the stream is complete.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It builds the first request path and query parameters, calls the API, extracts records from the response, and yields each non-empty batch. After the first page, it follows the response's `next` link and repeats. If Rippling replies with 401 or 403, it changes that low-level HTTP failure into a clear `StreamSkipped` message; other HTTP errors are passed upward unchanged.

**Call relations**: This method is the connector's handoff point to the wider sync engine: the engine asks it for pages, and it yields record batches back. Inside the loop it relies on `_initial_query` to start correctly, `_extract_records` to understand the response body, and `_next_path` to move from one page to the next. If authorization fails, it raises `StreamSkipped` so the larger run can treat that stream as unavailable instead of mistaking it for ordinary empty data.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

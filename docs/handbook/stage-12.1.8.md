# HR, recruiting, and workforce source connectors  `stage-12.1.8`

This stage is the doorway into HR and recruiting systems. It is used during the system’s data sync work, when the code reaches out to outside services, reads their records, and reshapes them into a steady stream the rest of the codebase can store or process. Each connector is like an adapter plug for a different vendor’s API, meaning its web-based way to ask for data.

The Ashby and Greenhouse connectors pull recruiting records such as candidates, jobs, applications, interviews, offers, and users. Recruitee does a similar job for hiring pipelines, including candidates, job offers, and departments. BambooHR focuses on employee operations, including staff lists, employee details, time off, timesheets, and field definitions. Deel reads contractor and workforce records such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team information. Together, these files hide the differences between many HR tools and turn their paged responses into consistent batches of records.

## Files in this stage

### Ashby recruiting streams
Ashby provides the first recruiting connector, exposing candidates, jobs, applications, interviews, offers, users, and lookup records as paged sync streams.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `source sync / API pagination`

Ashby is a recruiting system, and its API exposes data in pages rather than all at once. This file is the connector that knows Ashby’s rules: which endpoints exist, how to authenticate, how to ask for the next page, and how to read only records changed since a previous sync when Ashby supports that.

The stream list near the top is like a menu of Ashby data types the system can import. Each stream says the human-friendly stream name, the Ashby API path to call, the record’s main identifier, and sometimes the timestamp field used as a cursor. A cursor is a saved bookmark that lets the next sync continue from a known point instead of starting over.

The `AshbyConnector` class then supplies the Ashby-specific behavior. It builds an HTTP client using Ashby’s required Basic Authentication format, where the API key is used as the username and the password is empty. It also pages through Ashby’s POST-based list endpoints. Most streams follow the same pattern: send a limit, optionally send a sync token, yield records, then continue with Ashby’s next cursor until there is no more data.

One stream is special: `application_criteria_evaluations`. Ashby does not expose it as one simple list. The connector must first list applications, then ask for evaluations for each application. Without this file, the system would not know how to speak Ashby’s particular API shape.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for an Ashby data type. It keeps the stream catalog short and consistent, so each Ashby endpoint is described in the same way.

**Data flow**: It receives a stream name, an Ashby API path, and optional details such as the primary key and cursor field. It packages those details into a `StreamSpec`, which is the system’s standard description of a source stream, and returns that object for the connector’s stream list.

**Call relations**: This function is used while the file is being loaded to build the Ashby stream catalog. It hands the normalized stream information to `StreamSpec`, so the rest of the connector can later treat all streams in a common format.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to call Ashby’s API. Its main job is to translate the system’s stored credential into the exact Authorization header Ashby expects.

**Data flow**: It receives a base URL and a credential. If the credential already contains a special transport, such as a brokered proxy connection, it lets the parent connector build the client unchanged. Otherwise, it reads the API key from `credential.bearer`, encodes it as Basic Authentication with an empty password, and returns an HTTP client configured with that header. If no API key is available, it raises an error instead of making unauthenticated calls.

**Call relations**: The wider connector framework calls this when it needs a client for a sync. This method either delegates to the parent client builder for proxy-style credentials, or creates a new credential containing Ashby’s Basic Auth header before handing that back to the parent builder.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the router for reading pages from Ashby. It decides whether a stream can use the normal Ashby paging pattern or needs the special per-application lookup flow.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor. If the stream is `application_criteria_evaluations`, it ignores the normal cursor flow and yields pages produced by the special criteria-evaluation paginator. For every other stream, it passes the stream and cursor into the default paginator and yields each page it gets back.

**Call relations**: The connector framework calls this when it wants records for a stream. This function then hands control to either `AshbyConnector._paginate_default` for ordinary list endpoints or `AshbyConnector._paginate_application_criteria` for the nested application-evaluation endpoint.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ordinary Ashby list endpoints one page at a time. It follows Ashby’s `moreDataAvailable` and `nextCursor` signals until there is nothing left to fetch.

**Data flow**: It starts with a request body containing the page size. If a saved cursor exists, it sends that as Ashby’s `syncToken`, which asks Ashby for records changed since that token. It repeatedly sends POST requests to the stream’s endpoint, yields any records found in `results`, then uses `nextCursor` for the following request. It stops when Ashby says there is no more data or fails to provide a next cursor.

**Call relations**: This is called by `AshbyConnector.paginate` for the normal streams such as candidates, jobs, applications, interviews, and users. It relies on the connector’s POST helper to make each network request, then passes record batches back upward to the sync framework.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches application criteria evaluations, which require a two-step process in Ashby. It first finds application IDs, then asks Ashby for evaluations belonging to each application.

**Data flow**: It pages through `/application.list` to get applications. For each application record, it reads the application ID and posts that ID to `/application.listCriteriaEvaluations`. It copies each returned evaluation, makes sure it includes the related `applicationId`, groups the evaluations into batches, and yields those batches. It continues through application pages until Ashby reports no more applications or no next cursor is available.

**Call relations**: This is called by `AshbyConnector.paginate` only for the `application_criteria_evaluations` stream. It acts like a fan-out step: one page of applications can lead to many follow-up requests, and the resulting evaluation rows are then yielded back to the normal sync flow.

*Call graph*: called by 1 (paginate).


### Employee and contractor HR records
BambooHR and Deel cover core HR and contractor data, normalizing employees, contracts, time records, payslips, forms, and related metadata from varied API shapes.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `source sync / request handling`

BambooHR is an HR system, and this file is the read-only connector for pulling its data into UFO. The main problem it solves is that BambooHR does not behave like one simple, page-by-page API. Some endpoints return a full list inside a named field like `employees`; some return a plain list; employee detail requires first fetching the directory and then asking for each employee one by one; time-based endpoints require a start and end date. Without this connector, the rest of the system would not know which BambooHR URL to call, how to authenticate, or how to reshape the answers into normal record batches.

The file first defines the BambooHR streams, which are the kinds of data this source can produce. `BambooHRConnector` then creates an HTTP client with BambooHR’s required headers and authentication. BambooHR uses HTTP Basic authentication, which here means the API key is sent as the username and the literal password `x` is sent with it.

When the sync asks for a stream, `paginate` acts like a traffic director. It chooses the right fetch method for that stream. Each fetch method calls the appropriate BambooHR endpoint and yields records only when there is something to send. If BambooHR replies with “not allowed” errors, the connector reports that this stream should be skipped rather than crashing the whole source run.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a stream description for one kind of BambooHR data. A stream description tells the sync system the stream’s name, where it comes from, which field identifies each record, and which date fields can be used for progress tracking.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, created date field, updated date field, and whether it is a canonical stream. It fills in sensible defaults, especially using the stream name as the source object when none is given, and returns a `StreamSpec`, which is the system’s compact description of a readable data stream.

**Call relations**: This function is used while the module is loaded to build the `BAMBOOHR_STREAMS` list. It hands each completed stream description to `StreamSpec.__init__`, so the connector later knows which BambooHR streams it can offer.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to BambooHR. It applies BambooHR’s required JSON headers, request time limits, base URL, and authentication rules.

**Data flow**: It receives a base URL and a resolved credential. It trims extra slashes from the base URL, creates timeout settings, and prepares headers asking BambooHR for JSON. If the credential already has a custom transport, it keeps that transport. Otherwise, if the credential has an API key, it builds Basic authentication using that key as the username and `x` as the password. It returns an `httpx.AsyncClient`, which is the object used for asynchronous web requests. If there is no usable authentication information, it raises an error.

**Call relations**: The broader REST source machinery calls this when it is ready to make BambooHR requests. Inside, it relies on `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient` to produce a correctly configured web client before any stream fetching begins.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main dispatcher for reading a BambooHR stream. Even though the name says “paginate,” BambooHR usually returns whole result sets at once, so this function mainly chooses the right fetching routine and yields record batches from it.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value showing where a previous sync left off. It checks the stream name and sends the request to the matching helper: directory, employee detail, time off, timesheets, metadata fields, or custom reports. It yields each batch produced by that helper. If BambooHR refuses access with status 401 or 403, it changes that web error into a `StreamSkipped` signal explaining that the key is invalid or lacks permission. Unknown stream names raise a clear not-implemented error.

**Call relations**: The sync engine calls `paginate` when it wants records for a specific BambooHR stream. `paginate` then calls `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports` as appropriate. If BambooHR refuses access, it calls `StreamSkipped.__init__` by raising `StreamSkipped`, which lets the larger run continue without that stream.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches BambooHR’s employee directory, which is the broad list of employees returned by the directory endpoint. It is useful when the sync needs the directory as its own stream.

**Data flow**: It receives an HTTP client. It asks BambooHR for `/v1/employees/directory`, reads the `employees` list from the response, and yields that list as one batch if it is not empty. If BambooHR returns no employees, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. It performs the direct directory read and hands the resulting batch back up to `paginate`, which passes it onward to the sync system.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches detailed employee records. BambooHR requires a two-step process: first get the directory, then request each employee’s full details by ID.

**Data flow**: It receives an HTTP client. It first asks for `/v1/employees/directory` and reads the `employees` list. For each directory row that is a dictionary and has an `id`, it asks BambooHR for `/v1/employees/{id}`. If the detail response is a dictionary, it makes sure the record has the employee ID and yields that single detailed record as a one-item batch. Badly shaped rows or rows without IDs are skipped.

**Call relations**: `paginate` calls this when the requested stream is `employees`. This helper fans out from one directory call into many per-employee calls, then returns small batches to `paginate` so the sync can checkpoint progress frequently.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches time-off requests from BambooHR for a date range. It uses the sync cursor to avoid starting from scratch when possible.

**Data flow**: It receives an HTTP client and an optional cursor. It turns the cursor into BambooHR’s required `start` and `end` date parameters by calling `_date_window_params`. It then asks `/v1/time_off/requests/` for matching records. If BambooHR returns a plain list, it uses that list; otherwise it looks for a `requests` field. It yields the records as one batch if any are present.

**Call relations**: `paginate` calls this when the requested stream is `time_off_requests`. Before making the request, this function calls `_date_window_params` to prepare the date window BambooHR expects, then hands the resulting records back to `paginate`.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches timesheet entries from BambooHR for a date range. It supports incremental syncing by using the previous cursor as the start date.

**Data flow**: It receives an HTTP client and an optional cursor. It calls `_date_window_params` to create `start` and `end` parameters, then requests `/v1/time_tracking/timesheet_entries`. If the response is already a list, it uses it directly; otherwise it looks for an `entries` field. It yields one batch when there are records to send.

**Call relations**: `paginate` calls this when the requested stream is `timesheet_entries`. It depends on `_date_window_params` for the date filter, then returns the fetched timesheet records to `paginate` for delivery to the sync engine.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches BambooHR’s field catalog, which describes the available employee fields. This helps the system understand what fields BambooHR exposes.

**Data flow**: It receives an HTTP client. It asks BambooHR for `/v1/meta/fields`. If the response is a plain list, it uses that list; otherwise it looks for a `fields` field. If any field records are found, it yields them as one batch.

**Call relations**: `paginate` calls this when the requested stream is `meta_fields`. It performs the metadata request and returns the field records back through `paginate` to the rest of the sync flow.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks BambooHR to generate a custom employee report with a fixed set of useful fields. It is used when the sync wants this curated report rather than a raw endpoint.

**Data flow**: It receives an HTTP client. It builds a JSON request body with a report title and a selected list of employee fields, such as name, email, job title, department, supervisor, hire date, and employment status. It posts that body to `/v1/reports/custom`, reads the returned `employees` list, and yields those rows as one batch if any are present.

**Call relations**: `paginate` calls this when the requested stream is `custom_reports`. This helper performs the report request and hands the returned employee rows back to `paginate`, which passes them onward as stream records.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper turns a saved cursor into the `start` and `end` dates that BambooHR requires for time-based endpoints. It gives fresh syncs a very early start date so they can collect everything.

**Data flow**: It receives an optional cursor, usually a saved timestamp from an earlier sync. If the cursor is present and not blank, it takes the first 10 characters, which match the `YYYY-MM-DD` date part of an ISO-style timestamp. If there is no cursor, it uses `1970-01-01`. It always returns a dictionary with that `start` date and a far-future `end` date of `2100-01-01`.

**Call relations**: _fetch_time_off and `_fetch_timesheets` call this before contacting BambooHR. It gives both functions the date window they need, so they can ask BambooHR for records from the last known point forward.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `sync run`

This connector is a read-only bridge from Deel into the larger sync system. Deel exposes its data through a REST API, which means the connector must ask a web address for records, page by page, instead of receiving everything at once. Think of it like reading a long report one sheet at a time: the connector keeps asking for the next sheet until there are no more.

The file first defines which Deel record types are available as streams. A stream is one category of data to sync, such as contracts or timesheets. Most streams can be synced incrementally, meaning the connector asks only for records updated after the last saved cursor time. Forms are different because they do not use an update timestamp here, so they are fully refreshed each run.

The DeelConnector class sets the Deel API base address and describes how to fetch each page. It builds request parameters with a fixed page size, optionally adds an updated_after filter, calls the inherited web request helper, extracts records from Deel's response shape, and advances the offset until the last page is reached. There is no write path in this file: it only reads Deel data and hands records back to the sync framework.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is a small description of one Deel data stream. It keeps the stream definitions short and consistent, so the file can list Deel objects without repeating the same setup details each time.

**Data flow**: It receives a stream name and optional details such as the Deel API object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, such as using the stream name as the source object when none is given. It then returns a StreamSpec object that the connector later uses to know what to request and how to track progress.

**Call relations**: When the module defines DEEL_STREAMS, it calls this helper once for each Deel record type. The helper hands those settings to StreamSpec.__init__, which creates the stream description used later by DeelConnector.paginate.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the starting query parameters for a Deel API request. It decides whether the request should ask for all records or only records changed after a saved cursor.

**Data flow**: It takes a stream description and an optional cursor value, which is usually a saved timestamp from a previous sync. It always starts with the page limit set to 100. If there is both a cursor and the stream supports cursor-based syncing, it adds updated_after with that cursor value. It returns the parameter dictionary that will be sent with the web request.

**Call relations**: DeelConnector.paginate calls this before fetching pages. The returned parameters become the base request settings, and paginate adds the changing offset value for each page of results.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls usable record dictionaries out of a Deel API response. It protects the sync flow from unexpected response shapes by ignoring anything that is not a proper record object.

**Data flow**: It receives raw response data from the API. If the data is a dictionary with a data list inside, it keeps only the items in that list that are dictionaries. If the whole response is already a list, it applies the same filtering. If neither shape matches, it returns an empty list. The output is always a clean list of record dictionaries.

**Call relations**: DeelConnector.paginate calls this after each web request. Paginate uses the returned list to decide whether to yield records, stop because there are no records, or continue to the next page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read loop for a Deel stream. It requests one page of records at a time and yields each batch to the wider sync system until Deel has no more records to send.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the API path for that stream, prepares the base query parameters, and starts at offset 0. For each loop, it sends a GET request with the current offset, extracts records from the response, and yields them as a batch. If no records arrive, or if the batch is smaller than the page size, it stops. Otherwise it increases the offset by 100 and asks for the next page.

**Call relations**: The broader RestConnector-based sync machinery calls this when it needs records for a Deel stream. Inside the loop, it uses _initial_params to prepare the request and _extract_records to normalize each response before handing record batches back to the caller.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### Hiring pipeline platforms
Greenhouse and Recruitee read recruiting and hiring-pipeline entities such as candidates, jobs, applications, interviews, offers, departments, and users.

### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `source sync / API pagination`

Greenhouse exposes recruiting data through a web API. This file is the adapter between that API and UFO’s general source-sync machinery. Without it, the system would not know which Greenhouse URLs to call, how to authenticate, how to follow Greenhouse pagination, or how to fetch child records such as a candidate’s activity feed or a job’s openings.

The file first defines a catalog of streams. A stream is a named kind of data to copy, like "candidates" or "jobs_stages". Some streams are simple: one API path returns one list of records. Others are nested: the connector must first fetch parent records, then ask Greenhouse for each parent’s related records. For example, it fetches jobs, then fetches openings for each job, and stamps each opening with the job id so the relationship is not lost.

Authentication is also special. Greenhouse uses HTTP Basic authentication, where the API key is sent as the username and the password is blank. If credentials are supplied directly, this connector sets that up. If an auth proxy is being used, it leaves authentication to the proxy.

For pagination, Greenhouse uses a "Link" response header that points to the next page. This connector follows that chain until there are no more pages. If Greenhouse refuses access with a 401 or 403 status, the stream is marked as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a standard stream description for one Greenhouse data type. It keeps the long stream catalog readable by filling in common defaults, such as using "id" as the record key and using the stream name as the source object unless told otherwise.

**Data flow**: It receives a stream name plus optional details like the Greenhouse object name, primary key, cursor field, timestamp fields, and whether the stream is canonical. It packages those choices into a StreamSpec object, which the sync system later uses to know what the stream is called, how records are identified, and how incremental syncing should advance.

**Call relations**: The file calls this helper repeatedly while building the Greenhouse stream list. Each call produces a StreamSpec that is later exposed through GreenhouseConnector.streams_list, so the broader source framework can discover and sync those streams.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Greenhouse and adjusts authentication when the API key is available locally. Greenhouse expects the key as a Basic Auth username with an empty password, not as the usual bearer token header.

**Data flow**: It starts with a base HTTP client created by the parent RestConnector. If the resolved credential contains a bearer value, this function treats that value as the Greenhouse API key, installs HTTP Basic authentication on the client, and removes the normal Authorization header. It returns the prepared client, ready to make Greenhouse requests.

**Call relations**: The general connector setup calls this when it needs an HTTP client for a sync. It relies on the parent connector for the ordinary client setup, then swaps in Greenhouse-specific authentication by creating an httpx.BasicAuth object when needed.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This chooses the query parameter name Greenhouse expects for incremental syncing. Most streams use "updated_after", but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It checks the connector’s small exception table and returns the special parameter name if one exists, otherwise it returns the default "updated_after". Nothing else is changed.

**Call relations**: GreenhouseConnector.paginate calls this when it is about to request a simple stream with a saved cursor. That lets paginate add the correct server-side filter before handing the request to the page-walking code.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main page-producing function for every Greenhouse stream. Given a stream and an optional cursor, it decides which Greenhouse endpoint or endpoint pattern to use, then yields batches of records until the stream is done.

**Data flow**: It receives an HTTP client, a stream description, and possibly a cursor value from a previous sync. First it checks whether the stream is a nested per-parent stream. If so, it delegates to the per-parent paginator. If not, it looks up the simple API path, adds the page size, and adds an incremental cursor filter when appropriate. It then yields each page of records. If Greenhouse returns 401 or 403, it converts that access problem into a StreamSkipped signal so the run can record a skipped stream instead of treating it as a full failure.

**Call relations**: The source framework calls this while syncing a Greenhouse stream. Inside, it may ask _cursor_param which filter name to use, then hand off either to _paginate_link_header for normal endpoints or _paginate_per_parent for nested endpoints. If access is refused, it creates a StreamSkipped error for the caller to record.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This follows Greenhouse’s normal page-by-page response pattern. Greenhouse points to the next page using an HTTP Link header, and this function yields each page until that next link disappears.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the shared link-header pagination helper with Greenhouse’s fixed page size, then passes each returned list of records outward unchanged.

**Call relations**: GreenhouseConnector.paginate uses this for ordinary top-level streams. GreenhouseConnector._paginate_per_parent also uses it twice: first to walk parent records, and then to walk each parent’s child records.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches streams that live underneath another kind of record, such as openings under jobs or permissions under users. It preserves the parent-child relationship by adding the parent id onto each child record.

**Data flow**: It receives the parent endpoint, a child endpoint template, and the field name where the parent id should be stored. It pages through all parents, reads each parent’s id, then pages through that parent’s child endpoint. For each child record that is a dictionary-like object, it adds the parent id if the child does not already have it. It yields each child page after stamping those records.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is listed as a per-parent stream. This function repeatedly calls _paginate_link_header to do the actual HTTP page walking, using it first for parent pages and then for child pages.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `during source sync`

Recruitee is a recruiting service with a web API. This file is the adapter that lets UFO pull data from that API in a predictable way. Without it, the system would not know which Recruitee lists exist, where to request them, how to page through long results, or how to react when access is denied.

The file defines three readable streams: candidates, offers, and departments. A stream is simply one kind of list the sync can fetch. Candidates and offers are marked as main, or canonical, data; departments are also fetched but are not treated as a primary recall source in the same way.

The connector deliberately has an empty default base URL because Recruitee URLs include a company-specific tenant ID. That prevents the system from accidentally calling the wrong company’s API. The real full URL must be supplied by the sync setup.

Recruitee returns list results in numbered pages, like reading a book one page at a time. This connector asks for up to 100 records per page and keeps going until the shared REST helper stops producing pages. If Recruitee replies with “unauthorized” or “forbidden,” the connector does not crash the whole idea of syncing every source; it raises a clear “skip this stream” signal explaining that the credential is invalid or lacks permission.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Recruitee stream as a sequence of record pages. It is used when the sync system wants to read candidates, offers, or departments from Recruitee without needing to know Recruitee’s page-number API details.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. The cursor is ignored because these Recruitee streams are fetched as full refreshes rather than “only changes since last time.” It builds the stream path, asks the shared REST paging helper for pages of up to 100 records, and yields each page outward. If Recruitee responds with 401 or 403, it turns that failed web response into a StreamSkipped error with a human-readable reason; other HTTP errors are passed through unchanged.

**Call relations**: The broader source-sync framework calls this method when it is time to read a Recruitee stream. The method delegates the repetitive page-by-page fetching to the inherited REST helper, then hands each page back to the caller. When access is refused, it creates a StreamSkipped exception so the sync runner can treat the stream as unavailable because of permissions rather than as a normal data page.

*Call graph*: calls 1 internal fn (__init__).


### Workforce directory data
Rippling rounds out the stage by syncing company, worker, and team records from a workforce-management directory API.

### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `source sync pagination`

Rippling exposes business data through web API endpoints, but the system needs a consistent way to read that data stream by stream. This file is the adapter for that job. It defines which Rippling resources are available: companies, workers, and teams. Workers and teams can be read incrementally by asking for items updated after a saved timestamp, while companies are always read from the beginning because Rippling does not provide an update cursor for them.

The main class, RipplingConnector, inherits the common REST connector behavior used by the source framework. Think of it like a tour guide for Rippling’s API: it knows where to start, how many records to ask for at once, where to find the records in each response, and how to follow the “next page” sign until there are no more pages.

Rippling responses may wrap rows under different keys, such as the stream name or a generic data field, so this file carefully extracts only dictionary-shaped records and ignores anything unexpected. It also normalizes pagination links. If Rippling returns a full URL, the connector converts it back into just the path and query that the shared HTTP client can request.

Authentication is not stored here. The surrounding runner supplies an authorized HTTP client. If Rippling rejects the request with an authorization error, the connector raises StreamSkipped, meaning this stream should be skipped rather than treated like a normal data failure.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This function turns Rippling’s “next page” link into the path the connector should request next. It accepts either a full web address or a relative path, and returns nothing when there is no next page.

**Data flow**: It receives a next-page link, which may be missing, relative, or a complete URL. If the link is empty, it returns None. If it is a full URL, it pulls out only the path and query string, such as `/workers?cursor=...`, because the HTTP client already knows the base Rippling address. If it is already a relative link, it returns it unchanged.

**Call relations**: During pagination, RipplingConnector.paginate calls this after each API response to decide whether another page should be fetched. This function uses URL parsing so paginate can keep looping with a clean request path instead of worrying about full versus relative links.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the query parameters for the first request to a Rippling stream. It sets the page size and, when possible, adds an incremental update filter so the system does not reread old worker or team records.

**Data flow**: It receives the stream definition and an optional saved cursor value, usually a timestamp from the last successful sync. It always starts with a limit of 100 records. If the stream supports a cursor and a cursor value was provided, it adds `updatedAfter` with that timestamp. The result is a small dictionary of request parameters for the first API call.

**Call relations**: RipplingConnector.paginate calls this once before making the first request for a stream. After that first request, paginate clears the parameters because later pages are driven by Rippling’s own `next` links.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This function finds the actual row data inside a Rippling API response. It protects the rest of the sync from response-shape differences by returning a clean list of record dictionaries.

**Data flow**: It receives the decoded response body and the stream being read. If the response is a dictionary, it first looks for a list under the stream’s name, such as `workers` or `teams`. If that is not present, it looks for a list under `data`. If the entire response is already a list, it uses that. In every case, it keeps only items that are dictionaries and returns them as the records for that page; if nothing matches, it returns an empty list.

**Call relations**: RipplingConnector.paginate calls this after each HTTP response. The extracted records are what paginate yields to the rest of the source framework, while malformed or unexpected non-record items are quietly left out.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It fetches one page at a time, yields batches of records, follows Rippling’s next-page link, and turns authorization refusals into a clear “skip this stream” signal.

**Data flow**: It receives an authorized asynchronous HTTP client, a stream definition, and an optional cursor timestamp. It starts at the stream’s API path, builds the first request parameters, and repeatedly asks Rippling for data while there is a path to fetch. For each response, it extracts records and yields them as a batch if any are present. It then reads the response’s `next` link and converts it into the next path. If Rippling returns a 401 or 403 error, meaning the token is invalid or lacks permission, it raises StreamSkipped with an explanatory message; other HTTP errors are passed upward unchanged.

**Call relations**: The broader sync framework calls paginate when it wants records from one Rippling stream. Inside the loop, paginate relies on _initial_query to prepare the first request, _extract_records to turn each response into usable records, and _next_path to continue through pages. When permissions are missing, it hands control back to the framework by raising StreamSkipped instead of yielding data.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

# Recruiting, HR, and Workforce Source Connectors  `stage-14.1.7`

This stage is the set of “adapters” that lets the system bring in people and hiring data from outside services. It is used during the main sync work, when the system contacts a vendor’s web API, which is a structured way for software to ask another service for data, and turns the replies into records it can store and search.

Each file knows the habits of one service. The Ashby, Greenhouse, and Recruitee connectors focus on recruiting: candidates, jobs, applications, interviews, offers, departments, users, and related lookup details. Greenhouse also knows how to fetch smaller child records that belong to larger items, such as notes or questions tied to a candidate or job. BambooHR covers employee records, time off, timesheets, company metadata, and custom reports, smoothing out BambooHR’s varied response shapes. Deel reads contractor and HR operations data such as contracts, payslips, tasks, forms, and timesheets. Rippling reads companies, workers, and teams. Together, these connectors act like translators, turning many different vendor formats into steady streams the wider sync system can process.

## Files in this stage

### Recruiting platforms
Applicant tracking and recruiting connectors that sync candidates, jobs, applications, interviews, offers, and related hiring records.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `during source sync, while reading pages from Ashby's API`

Ashby is a recruiting platform, and its API exposes many different lists: candidates, jobs, applications, users, and so on. This file defines those lists as named streams, then provides the connector code that logs in, asks Ashby for pages of data, and keeps asking until there is no more data left.

The main idea is simple: Ashby list endpoints work like a book split into pages. The connector sends a POST request with a page size, receives some records plus a pointer to the next page, then repeats with that pointer. For streams that support incremental syncing, it can also send a previous cursor as Ashby's sync token, meaning “only give me things changed since last time.”

Authentication has one Ashby-specific twist. Ashby expects HTTP Basic authentication, where the API key is used as the username and the password is empty. If the credential already includes a proxy transport, the connector leaves it alone; otherwise it builds the needed Basic Authorization header itself.

Most streams use the same paging routine. One stream, application_criteria_evaluations, is different: Ashby only returns those evaluations when asked for one application at a time. So the connector first lists applications, then asks for criteria evaluations for each application id and stamps the application id onto each returned evaluation.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for an Ashby API resource. It keeps the catalog of Ashby streams compact and consistent, so each stream records its name, endpoint path, primary key, timestamp fields, and whether it is a main canonical stream.

**Data flow**: It receives human-readable stream settings, such as the stream name, Ashby endpoint path, primary key, and optional cursor field. It packages those settings into a StreamSpec object. The result is a reusable description that tells the connector where to call Ashby and how to identify or order the records it receives.

**Call relations**: This helper is used while the file defines ASHBY_STREAMS. Each call produces one StreamSpec, and the AshbyConnector later uses those stream definitions when deciding which endpoint to read and how to paginate it.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to Ashby, with the correct authentication attached. Its main job is to turn a plain Ashby API key into the Basic Authorization header Ashby expects.

**Data flow**: It receives a base URL and a Credential object. If the credential already has a custom transport, it passes the credential through unchanged to the parent connector. Otherwise it reads the API key from the credential, encodes it in the Basic-auth format of `api_key:` using base64, and builds a new credential with an Authorization header. It returns an asynchronous HTTP client ready to make requests.

**Call relations**: The wider RestConnector setup calls this when it needs a client for Ashby. This method either delegates to the parent client builder directly, or prepares Ashby-specific credentials first and then hands that prepared credential to the parent builder.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method chooses the right paging strategy for a stream and yields batches of records. Most Ashby streams can be read with the standard page-by-page method, but application criteria evaluations need a special per-application lookup.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from a previous sync. It checks the stream name. For the special criteria-evaluation stream, it asks the per-application paginator for pages. For all other streams, it asks the default paginator to read the stream endpoint, passing along the cursor. It yields each list of records that those helper methods produce.

**Call relations**: The sync engine calls this when it wants records for an Ashby stream. This method acts like a traffic director: it sends ordinary streams to AshbyConnector._paginate_default and sends the special application_criteria_evaluations stream to AshbyConnector._paginate_application_criteria.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads ordinary Ashby list endpoints one page at a time. It supports both full backfills and incremental reads by including a sync token when a previous cursor is available.

**Data flow**: It starts with a request body containing the page limit. If a cursor is provided, it adds that cursor as Ashby's syncToken. It then repeatedly posts to the stream's Ashby endpoint, yields any records found in the `results` field, and checks whether Ashby says more data is available. If there is another page, it sends Ashby's nextCursor on the next request. It stops when Ashby reports no more data or fails to provide a next cursor.

**Call relations**: AshbyConnector.paginate calls this for every normal stream. It is the workhorse for candidates, jobs, applications, users, and most other Ashby resources, repeatedly making POST requests through the connector's shared request helper.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads application criteria evaluations, which Ashby exposes only by application id. It first walks through all applications, then asks Ashby for the criteria evaluations belonging to each one.

**Data flow**: It requests pages from `/application.list`. For each application record, it extracts the application id. If an id exists, it posts to `/application.listCriteriaEvaluations` with that id. It copies each returned evaluation into a new row and adds the applicationId if it is missing, so the evaluation can still be tied back to its application. It yields non-empty batches of these stamped evaluation rows. It keeps paging through applications until Ashby says there are no more.

**Call relations**: AshbyConnector.paginate calls this only for the application_criteria_evaluations stream. It performs a fan-out pattern: one application-list request leads to many per-application detail requests, and the resulting evaluation records are yielded back to the sync flow.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `sync read phase`

Greenhouse stores recruiting information such as candidates, jobs, applications, interviews, offers, users, scorecards, and reference lists. This connector is the adapter that turns those Greenhouse API endpoints into named streams the rest of the system can sync.

Most Greenhouse endpoints return a plain list of records, like a stack of forms with no outer wrapper. The connector therefore mostly passes those records through as-is. It also defines a large stream catalog so the sync runner knows what can be read, which field identifies each record, and which date field can be used for incremental syncing. Incremental syncing means “only ask for records changed since last time,” instead of downloading everything again.

Greenhouse uses HTTP Basic authentication in an unusual way: the API key is used as the username and the password is empty. If the credential is available directly, this file sets that up. If authentication is being supplied by a broker or proxy, it leaves the base client alone.

Pagination is also important. Greenhouse sends a `Link` header that points to the next page of results, so the connector follows that trail until there are no more pages. Some streams are nested: for example, job openings belong to each job. For those, the connector first reads all parent records, then asks for each parent’s children and stamps each child with the parent id so its origin is not lost. If Greenhouse refuses access to a stream, the connector marks that stream as skipped instead of failing the whole run.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream description for one Greenhouse collection. A stream description tells the sync system the stream’s name, where it comes from, how records are identified, and which time fields can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, created time field, updated time field, and whether the stream is a main canonical stream. It fills in sensible defaults, then returns a `StreamSpec`, which is the project’s small record of how that stream should be read.

**Call relations**: This helper is used while the module is loaded to build all the Greenhouse stream constants. It hands those stream descriptions to the connector through `ALL_STREAMS`, so later the sync runner can ask `GreenhouseConnector.paginate` to read each stream.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Greenhouse. Its main job is to set Greenhouse’s required Basic authentication when the API key is available directly.

**Data flow**: It starts with a base HTTP client made by the parent connector class and a resolved credential. If the credential contains a bearer value, this function treats that value as the Greenhouse API key, installs HTTP Basic authentication with an empty password, and removes any bearer-token authorization header that would be wrong for Greenhouse. It returns the ready-to-use client.

**Call relations**: The connector framework calls this when it needs a network client for Greenhouse. This method builds on the parent `RestConnector` setup, then applies the Greenhouse-specific authentication shape using `httpx.BasicAuth`.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This small helper chooses the query parameter name Greenhouse expects for incremental syncing. Most streams use `updated_after`, but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It checks the file’s special-case map and returns that stream’s custom cursor parameter if one exists; otherwise it returns the default `updated_after`.

**Call relations**: `GreenhouseConnector.paginate` calls this when it is about to request an incremental stream. The returned parameter name is added to the outgoing API request so Greenhouse filters records on the server side.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read path for a Greenhouse stream. Given a stream and an optional cursor value from the last sync, it yields pages of records from the right Greenhouse endpoint.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. First it checks whether the stream is a nested child stream, such as openings under jobs; if so, it delegates to the per-parent reader. Otherwise it looks up the stream’s normal API path, builds request parameters including `per_page=500`, and adds the right cursor filter when incremental syncing is possible. It then yields each page returned by the link-header paginator. If Greenhouse responds with a permission or authentication refusal, it converts that into a `StreamSkipped` signal; other HTTP errors still bubble up as failures.

**Call relations**: The sync runner calls this function when it wants data for a particular Greenhouse stream. Inside, it chooses between `_paginate_per_parent` for nested streams and `_paginate_link_header` for normal streams, and uses `_cursor_param` to name the incremental filter correctly.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper follows Greenhouse’s page-by-page navigation for one endpoint. It keeps asking for the next page until Greenhouse stops providing a “next” link.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It passes those to the shared REST pagination helper with the Greenhouse page size, then yields each list of records it receives.

**Call relations**: `GreenhouseConnector.paginate` uses this for ordinary top-level streams. `_paginate_per_parent` also uses it twice: once to walk parent records and again to walk each parent’s child collection.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads nested Greenhouse data, where records are attached to another record. For example, it can read every job first, then read the openings for each job.

**Data flow**: It receives a parent endpoint path, a child endpoint template, and the field name where the parent id should be recorded. It pages through the parent collection, takes each parent’s `id`, requests that parent’s child collection, and adds the parent id to each child record if it is not already present. It yields child pages outward for the rest of the sync to store.

**Call relations**: `GreenhouseConnector.paginate` calls this when the requested stream is one of the known per-parent streams. This function relies on `_paginate_link_header` for both parent and child API calls, then hands the stamped child records back to `paginate`.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `during source sync, when fetching Recruitee stream pages`

This connector is the bridge between UFO and Recruitee, a recruiting platform. Its job is read-only: it fetches data from Recruitee and does not try to create or update anything there.

The file defines the Recruitee streams the system knows about: candidates, offers, and departments. A stream is one kind of list to sync. Each stream says which API object to request and which field acts as the stable ID, so the rest of the system can recognize the same record again later.

Recruitee’s API returns results in numbered pages, like flipping through a catalog: page 1, page 2, page 3, and so on, with up to 100 records per page. The connector keeps asking for pages until the API returns a short final page.

One important safety choice is that the base URL is empty by default. Recruitee URLs include a company-specific tenant ID, so the real URL must be supplied by the sync setup. This avoids accidentally calling the wrong company’s API.

If Recruitee responds with 401 or 403, meaning “not allowed” or “bad credentials,” the connector marks that stream as skipped with a clear message. Other HTTP errors are allowed to bubble up as real failures.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream in batches of records. It hides the page-by-page API details so the rest of the sync can simply receive lists of records until the stream is finished.

**Data flow**: It receives an HTTP client, a stream description such as candidates or offers, and an optional cursor value that is not used here because Recruitee streams are full-refresh only. It asks Recruitee for numbered pages under the stream’s API path, expecting the records to be wrapped under the stream name. Each page of records is yielded outward. If Recruitee refuses access with status 401 or 403, it changes that low-level HTTP error into a clear StreamSkipped result explaining that the credential or permission scope is not good enough.

**Call relations**: The broader REST source machinery calls this method when it needs records for a Recruitee stream. Inside the method, page retrieval is delegated to the shared page-number helper provided by the base connector, while refusal errors are handed to StreamSkipped so the sync can skip that stream cleanly instead of crashing with a vague HTTP error.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce systems
People operations, contractor, payroll-adjacent, and workforce directory connectors that sync employees, workers, contracts, timesheets, payslips, teams, and related HR data.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `source sync runtime`

BambooHR is an HR system, and its API does not behave like a simple page-by-page list. Some endpoints return one big list, some wrap records under names like "employees" or "entries", and employee details require first reading the directory and then asking for each employee one by one. This file hides those differences behind one connector class, BambooHRConnector.

At the top, it defines the streams this source can produce, such as the employee directory, employee details, time-off requests, timesheet entries, metadata fields, and a custom report. A stream is a named feed of records, like a labeled folder in an export.

The connector also knows how to create an HTTP client for BambooHR. BambooHR needs Basic authentication, where the API key is used as the username and the password is the literal "x". It also explicitly asks for JSON, because BambooHR otherwise may return XML.

The main routing point is paginate. Despite the name, BambooHR does not really paginate here; instead, this method chooses the right fetch method for the requested stream. If BambooHR refuses access with 401 or 403, the connector marks that stream as skipped instead of crashing the whole sync. Without this file, the project would not know BambooHR’s endpoint shapes, authentication rules, or date-window requirements.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This small helper creates a StreamSpec, which is the project’s description of one named BambooHR feed. It keeps the stream definitions short and consistent, so each stream can say what its record ID and time fields are without repeating boilerplate.

**Data flow**: It receives a stream name and optional details such as the BambooHR object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses to advertise and sync that stream.

**Call relations**: This helper is used while the file is being loaded to build BAMBOOHR_STREAMS. It hands each finished StreamSpec to the connector class through the streams_list class setting.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method builds the HTTP client used to talk to BambooHR. It applies BambooHR-specific rules: use the tenant’s base URL, request JSON, set safe timeouts, and authenticate with the API key when one is present.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares JSON headers and timeout settings, then either uses a provided proxy transport or creates Basic authentication from the credential’s API key. It returns an async HTTP client ready for BambooHR requests, or raises an error if no usable authentication is available.

**Call relations**: The wider REST connector infrastructure calls this when a BambooHR sync starts and needs a network client. It relies on httpx to create the client, timeout object, and BasicAuth object that will be used by the later fetch methods.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for all BambooHR streams. Given a requested stream, it chooses the matching fetch method and yields batches of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It checks the stream name, calls the matching private fetch method, and passes along each returned batch. If BambooHR rejects the request with an unauthorized or forbidden status, it turns that into a StreamSkipped signal with a clear explanation.

**Call relations**: The sync engine calls this when it wants records for a stream. paginate then calls _fetch_directory, _fetch_employees, _fetch_time_off, _fetch_timesheets, _fetch_meta_fields, or _fetch_custom_reports depending on the stream name, and sends their batches back upstream.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR’s employee directory endpoint. The directory is the broad list of employees, useful on its own and also as the starting point for fetching detailed employee records.

**Data flow**: It sends a GET request to the directory endpoint, looks for the list under the "employees" key, and yields that list as one batch if it contains records. If the response has no employees, it yields nothing.

**Call relations**: paginate calls this when the requested stream is employees_directory. The records it yields go straight back through paginate to the sync engine.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches detailed information for each employee. BambooHR does not provide all details in the directory list, so this method first gets the directory and then asks for each employee by ID.

**Data flow**: It reads the employee directory, loops over each dictionary-like row, extracts the employee ID, and requests that employee’s detail endpoint. For each valid detail response, it makes sure the ID is present and yields a one-record batch.

**Call relations**: paginate calls this when the requested stream is employees. It depends on the directory endpoint as its starting list, then produces immediate small batches so the sync can make progress employee by employee.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads time-off requests from BambooHR. Because BambooHR requires a date range for this endpoint, it converts the sync cursor into start and end query parameters before requesting data.

**Data flow**: It receives the HTTP client and an optional cursor. It asks _date_window_params to turn that cursor into BambooHR date parameters, sends a GET request for time-off requests, then accepts either a plain list response or a response with records under "requests". If records exist, it yields them as one batch.

**Call relations**: paginate calls this for the time_off_requests stream. Before making the request, it calls _date_window_params so the endpoint receives the date window BambooHR expects.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads timesheet entries from BambooHR. Like time off, this endpoint needs a start and end date, so the method prepares a date window before making the request.

**Data flow**: It receives the HTTP client and optional cursor, converts the cursor into start and end query parameters with _date_window_params, and requests the timesheet endpoint. It handles either a plain list response or records under "entries", then yields the records if any are present.

**Call relations**: paginate calls this for the timesheet_entries stream. It relies on _date_window_params for the shared BambooHR date-window rule used by both timesheets and time-off requests.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR’s field catalog, which describes available employee fields. That catalog helps the system understand what kinds of fields BambooHR exposes.

**Data flow**: It sends a GET request to the metadata fields endpoint. It accepts either a plain list or a response where the list is under "fields", then yields the list if it is not empty.

**Call relations**: paginate calls this when the requested stream is meta_fields. Its output flows back through paginate as the records for that stream.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method asks BambooHR to build a custom employee report with a specific set of useful fields. It is used when the system wants a compact, chosen view of employee data rather than every possible field.

**Data flow**: It creates a request body naming the report and listing fields such as employee ID, display name, email, department, supervisor, and hire date. It sends that body with a POST request to the custom report endpoint, reads returned rows from the "employees" key, and yields them if present.

**Call relations**: paginate calls this for the custom_reports stream. The method packages the report request, sends it to BambooHR, and returns the resulting employee rows back through paginate.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper creates the date range BambooHR requires for time-off and timesheet requests. It turns a previous sync cursor into a simple YYYY-MM-DD start date.

**Data flow**: It receives an optional cursor. If there is no cursor, it starts at 1970-01-01 so a first sync can collect everything; if there is a cursor, it trims it and keeps the first ten characters as the date. It returns a dictionary with that start date and a far-future end date of 2100-01-01.

**Call relations**: _fetch_time_off and _fetch_timesheets call this just before making their API requests. It centralizes the shared date-window rule so both streams ask BambooHR for data in the same way.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `during source sync, while fetching pages from Deel`

Deel is an HR platform, and its API returns records in pages, much like a website showing 100 search results at a time. This file defines a read-only connector for that API. Without it, the system would not know which Deel objects to fetch, how to ask for the next page, or how to do smaller follow-up syncs instead of re-reading everything.

The file first defines the list of Deel streams the system cares about: contracts, forms, payslips, timesheets, and tasks. A stream is a named kind of data to sync. Most streams can be updated incrementally using an `updated_at` value, meaning the connector can ask Deel for only records changed after the last saved cursor. Forms are the exception, so they are refreshed in full each run.

`DeelConnector` then supplies the practical details: the Deel API base address, the stream list, and pagination rules. For each stream, it builds query parameters with a fixed page size of 100 and, when possible, an `updated_after` filter. It calls the inherited HTTP GET helper, extracts records from Deel's response shape, yields each page, and stops when Deel returns no records or fewer than 100 records. The connector only reads data; it does not create or change anything in Deel.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a `StreamSpec`, which is the system's description of one kind of Deel data to sync. It keeps the stream definitions short and consistent, so each Deel object can say its name, primary key, cursor field, and whether it is canonical.

**Data flow**: It takes a stream name and optional details such as the Deel API object name, primary key, cursor field, and canonical flag. It fills in sensible defaults, then produces a `StreamSpec` object that the connector later uses to know what endpoint to call and how to track progress.

**Call relations**: This helper is used while building the file's stream list. It hands each completed stream description to the rest of the connector setup, so `DeelConnector` can later loop through those streams during sync.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This method builds the starting query parameters for a Deel API request. It always asks for up to 100 records, and when a saved cursor is available it asks Deel for only records updated after that point.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It creates a parameter dictionary with `limit` set to the page size, adds `updated_after` only when both the cursor and the stream's cursor field exist, and returns that dictionary for the request.

**Call relations**: `DeelConnector.paginate` calls this before requesting pages from Deel. The returned parameters become the base request settings, and `paginate` adds the changing `offset` value as it moves through the pages.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This method pulls usable record objects out of a Deel API response. It is defensive: it accepts Deel's normal `{data: [...]}` shape, also tolerates a plain list, and ignores anything that is not a dictionary-like record.

**Data flow**: It receives raw response data from the API. If the data is a dictionary with a list under `data`, it keeps only the list items that are record dictionaries. If the whole response is already a list, it does the same filtering there. If neither shape fits, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each HTTP request. Its result decides both what records get yielded to the sync system and whether pagination should continue or stop.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method walks through all pages for one Deel stream and yields batches of records. It hides Deel's offset-based pagination from the rest of the system, so callers can simply receive page after page until there is nothing left.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the endpoint path, prepares base query parameters, starts at offset 0, then repeatedly requests a page from Deel. Each response is cleaned into records, yielded as a batch, and the offset is increased by 100 until the response is empty or shorter than a full page.

**Call relations**: The broader source-sync machinery calls this when it needs data for a particular Deel stream. Inside the loop, it relies on `_initial_params` to prepare request filters and `_extract_records` to turn raw API responses into usable records before handing each batch back to the sync flow.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `source sync pagination`

Rippling is an external HR and company-data service, and its API returns large lists in pages rather than all at once. This file is the adapter that knows Rippling's particular rules: which streams exist, where to ask for them, how to request only recently updated workers or teams, and how to follow the API's “next page” link until there is nothing left to fetch.

The connector defines three readable streams: companies, workers, and teams. Workers and teams can be read incrementally using an update timestamp, which means the system can ask “what changed since last time?” Companies do not have that kind of cursor here, so they are read with a full refresh.

The main work happens in `paginate`. It builds the first API request, downloads a page, pulls usable record dictionaries out of whatever response wrapper Rippling used, yields those records to the caller, then follows the next-page pointer. Think of it like reading a book by following “continue on page…” notes until the story ends.

If Rippling refuses access with a 401 or 403 response, the connector turns that into `StreamSkipped`, meaning this stream should be skipped because the token is invalid or lacks permission. This file only reads from Rippling; it intentionally contains no write or update behavior.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling's next-page link into a path the HTTP client can request. Rippling may return either a full web address or just a relative path, so this function normalizes both forms.

**Data flow**: It receives a next-page link, which may be missing, absolute, or relative. If the link is missing or unusable, it returns nothing. If the link is a full URL, it keeps only the path and query string; if it is already relative, it returns it as-is.

**Call relations**: `paginate` calls this after each API response to decide where to go next. It uses `urlparse` to safely split full URLs into their pieces before handing the cleaned path back to the pagination loop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first request to a Rippling stream. It sets the page size and, when possible, adds a timestamp so the API returns only records updated after the last sync point.

**Data flow**: It receives the stream description and an optional cursor timestamp. It always starts with a limit of 100 records per page. If the stream supports a cursor and a cursor value was provided, it adds `updatedAfter`; the result is a dictionary of query parameters for the first API call.

**Call relations**: `paginate` calls this once before making the first request for a stream. After that first request, the pagination loop follows Rippling's own next links instead of rebuilding the query each time.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the actual list of records inside a Rippling API response. It protects the rest of the sync code from small differences in response shape.

**Data flow**: It receives raw response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream's name, such as `workers`, then under a generic `data` key. If the response itself is a list, it uses that. In all cases, it keeps only dictionary-like records and returns them as a list.

**Call relations**: `paginate` calls this after every downloaded page. The cleaned records it returns are what `paginate` yields onward to the sync system.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for a Rippling stream. It repeatedly asks Rippling for pages of data, yields each non-empty batch of records, and stops when Rippling no longer provides a next-page link.

**Data flow**: It receives an async HTTP client, a stream description, and an optional cursor timestamp. It builds the first path and query, sends a GET request through the base REST connector, extracts records from the response, yields those records to the caller, then follows the response's `next` link. If Rippling rejects the request with 401 or 403, it raises `StreamSkipped` with a clear permission or credential message; other HTTP errors are passed through.

**Call relations**: The wider sync runner calls this when it wants to read one Rippling stream. Inside the loop it relies on `_initial_query` to prepare the first request, `_extract_records` to turn each response into usable rows, and `_next_path` to continue from one page to the next. When access is refused, it hands control back to the sync framework by raising `StreamSkipped` instead of treating the refusal as ordinary data.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

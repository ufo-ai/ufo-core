# Finance, billing, HR, recruiting, and operations source connectors  `stage-16.7`

This stage is a set of read-only “connectors,” which are small adapters that know how to fetch data from outside business tools without changing it. It is behind-the-scenes support for the sync system: each connector logs in to a service, asks its web API for records, follows page-by-page results, and reshapes them into standard streams the rest of the project can process.

The recruiting connectors cover Ashby, Greenhouse, and Recruitee, pulling candidates, jobs, applications, interviews, offers, and hiring lookup data. The HR connectors cover BambooHR, Deel, and Rippling, bringing in employee records, contracts, payslips, teams, workers, tasks, and related data. The finance and operations connectors cover Brex for spend data, QuickBooks and Xero for accounting, Chargebee and Recurly for subscription billing, and Stripe and Square for payments, orders, customers, invoices, inventory, and commerce records. Together, these files act like different plug adapters for different wall sockets: each understands one vendor’s shape, but all deliver records in the same usable form.

## Files in this stage

### Recruiting sources
Connectors that sync candidate, job, application, interview, and offer data from applicant tracking systems.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `source sync`

Ashby exposes its recruiting data through a web API where each list request is a POST request, and large result sets arrive in pages. This file wraps those rules so the rest of the project does not need to know Ashby’s details. Think of it like a librarian who knows which Ashby shelf to visit, how many books to take at once, and how to follow the “next shelf” note until there are no more books.

The file first defines the Ashby streams the connector can read. A stream is one kind of data, such as candidates or jobs, with details like its API path, its main identifier, and which timestamp can be used for incremental syncing. Incremental syncing means asking for “only what changed since last time” instead of rereading everything.

The `AshbyConnector` then supplies Ashby-specific behavior. It creates an authenticated HTTP client using Ashby’s API-key-as-Basic-auth rule. For normal streams, it repeatedly posts to Ashby’s list endpoint, sends the saved cursor as a sync token when available, yields each batch of records, and follows Ashby’s `nextCursor` while more data exists.

One stream is special: criteria evaluations are not listed directly on their own. The connector first lists applications, then asks Ashby for evaluations for each application ID and adds that application ID to each returned evaluation when needed.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Ashby resource. It keeps the stream catalog readable by filling in shared defaults, such as the usual created and updated timestamp fields.

**Data flow**: It receives a friendly stream name, the Ashby API path, and optional details like the primary key and cursor field. It packages those choices into a `StreamSpec`, which is the object the connector uses later to know what endpoint to call and how to identify records.

**Call relations**: This function is used while the file is loaded to build `ASHBY_STREAMS`. Its direct handoff is to `StreamSpec.__init__`, which stores the stream settings for the connector to use during syncing.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Ashby, with the authentication format Ashby expects. Ashby uses HTTP Basic authentication, where the API key is placed where a username would normally go and the password is empty.

**Data flow**: It receives a base URL and a resolved credential. If the credential already includes a custom transport, it leaves that setup to the parent connector. Otherwise it reads the API key from `credential.bearer`, encodes `api_key:` using Base64, puts that into an `Authorization: Basic ...` header, and returns an async HTTP client configured with that header. If no API key is present, it raises an error instead of making unauthenticated requests.

**Call relations**: This method is the Ashby-specific version of the client-building hook inherited from `RestConnector`. Inside this flow it creates a new `Credential` containing the Basic-auth header and uses `base64.b64encode` to produce the encoded token Ashby requires.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This chooses the right paging strategy for a stream and yields batches of Ashby records. Most streams follow the same paging pattern, but criteria evaluations need a special per-application lookup.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. If the stream is `application_criteria_evaluations`, it ignores the normal cursor path and yields pages from the special fan-out method. For every other stream, it passes the stream and cursor into the normal Ashby pagination method and yields each page it gets back.

**Call relations**: This is the public paging entry used by the connector framework when it wants records from a stream. It dispatches either to `AshbyConnector._paginate_application_criteria` for the special nested stream or to `AshbyConnector._paginate_default` for ordinary Ashby list endpoints.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one normal Ashby list endpoint from start to finish. It understands Ashby’s page format: send a limit, optionally send a sync token, then keep following `nextCursor` while Ashby says more data is available.

**Data flow**: It starts with a request body containing the page size. If a saved cursor was provided, it adds that as `syncToken` so Ashby can return changed records. It posts to the stream’s endpoint, extracts the `results` list, yields that list when it is not empty, then checks `moreDataAvailable`. If more data exists and Ashby supplied `nextCursor`, it sends that cursor in the next request. The output is an asynchronous sequence of record batches; it stops when Ashby has no more pages or fails to provide the next cursor.

**Call relations**: This method is called by `AshbyConnector.paginate` for ordinary streams such as candidates, jobs, users, and offers. It performs the repeated Ashby POST requests and hands each batch of records back up to `paginate`, which then yields them to the broader sync process.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads criteria evaluations, which Ashby stores under individual applications rather than as one simple list. It first lists applications, then asks for the evaluation records belonging to each application.

**Data flow**: It pages through `/application.list` using Ashby’s normal cursor pattern. For each application record, it reads the application `id`; if the ID is missing, it skips that application. It then posts to `/application.listCriteriaEvaluations` with that ID, gathers the returned evaluations, copies each evaluation into a new row, and makes sure the row includes `applicationId`. It yields batches of stamped evaluation rows and stops when there are no more application pages.

**Call relations**: This method is called by `AshbyConnector.paginate` only for the `application_criteria_evaluations` stream. It acts as a small two-step journey: first get application IDs, then use each ID to fetch its related evaluation records before handing those records back to the sync flow.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `source sync / external API reading`

Greenhouse exposes recruiting information through many web API endpoints: candidates, jobs, applications, interviews, offers, users, and supporting lookup data. This file turns those endpoints into named streams that the rest of the system can sync in a consistent way. Think of it like a route map for a delivery driver: it says which roads exist, which stops are important, and how to keep following the “next page” signs until all packages are collected.

Most Greenhouse endpoints return one flat list of records at a time, so the connector can pass those records through without unwrapping a special response shape. Some data is nested under a parent item, such as job openings under a job or permissions under a user. For those, the connector first reads the parent list, then visits each parent’s child endpoint, and adds the parent id onto each child record so the relationship is not lost later.

The connector also supports incremental syncing, meaning it can ask Greenhouse only for records changed after a saved timestamp. Different Greenhouse endpoints use slightly different query parameter names for that timestamp, so the file records those exceptions. If Greenhouse says the API key is not allowed to read a stream, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream description for one Greenhouse data set. A stream description tells the sync engine the stream’s public name, where it comes from in Greenhouse, what field uniquely identifies records, and which timestamp can be used for incremental syncing.

**Data flow**: It receives the stream name and optional details such as the source endpoint name, primary key, cursor timestamp field, and whether the stream is one of the main canonical streams. It fills in sensible defaults where details are not provided, then returns a StreamSpec object that the connector later uses as its instructions for syncing that stream.

**Call relations**: This helper is used while the module is loaded to build all of the Greenhouse stream definitions. Those StreamSpec objects are collected into the connector’s stream list, which is what the broader source-sync system sees when it asks GreenhouseConnector what it can read.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the web client used to call Greenhouse. Greenhouse expects HTTP Basic authentication, which is a standard username-and-password style login; here the API key is used as the username and the password is blank.

**Data flow**: It receives a base URL and a resolved credential. First it asks the base RestConnector to create the normal HTTP client. If the credential contains a bearer value on this machine, it replaces the usual bearer-token header with Greenhouse-style Basic authentication. If authentication is being supplied elsewhere, such as by an auth proxy, it leaves the base client alone. The result is an httpx AsyncClient ready to make Greenhouse requests.

**Call relations**: The wider connector framework calls this when it needs a client for a sync run. This method customizes the generic REST client just enough for Greenhouse’s authentication rules, then hands that client back to the framework and to pagination methods that perform the actual requests.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This picks the correct Greenhouse query parameter name for incremental syncing. Most streams use updated_after, but a few Greenhouse endpoints use different names, so this function hides that small but important difference.

**Data flow**: It receives a stream name. It checks a small lookup table for special cases such as applications and eeoc. If the stream is not listed there, it returns the default parameter name, updated_after.

**Call relations**: GreenhouseConnector.paginate calls this when it is about to request an incremental stream with a saved cursor value. The returned parameter name is added to the API request so Greenhouse knows where to start returning changed records.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading routine for a Greenhouse stream. It decides which API path to call, adds paging and incremental-sync options, and yields records page by page to the rest of the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp from a previous sync. If the stream is a nested per-parent stream, it delegates to the parent-child pagination routine. Otherwise it finds the simple Greenhouse endpoint path, builds request parameters such as per_page and the cursor filter, then follows Greenhouse’s page links and yields each page of records. If Greenhouse responds with 401 or 403, meaning unauthorized or forbidden, it turns that into a StreamSkipped result instead of a hard failure.

**Call relations**: The source framework calls this whenever it wants records for a particular Greenhouse stream. Inside, it uses _cursor_param to name incremental filters correctly, _paginate_link_header for ordinary endpoints, and _paginate_per_parent for nested endpoints. It hands completed pages back upward to the sync engine.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This follows Greenhouse’s normal pagination style. Pagination means the API returns records in chunks, and a Link header points to the next chunk when more data exists.

**Data flow**: It receives the HTTP client, an endpoint path, and optional query parameters. It calls the shared REST pagination helper with Greenhouse’s page size of 500 records, then yields each returned page unchanged.

**Call relations**: GreenhouseConnector.paginate uses this for simple top-level streams. GreenhouseConnector._paginate_per_parent also uses it twice: first to read parent records and then to read each parent’s child records. This function keeps the Greenhouse-specific page size and link-header behavior in one place.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that are not available as one single top-level list. For example, to get openings for jobs, it first lists jobs and then asks Greenhouse for the openings under each job.

**Data flow**: It receives a parent endpoint path, a child endpoint template containing a parent id slot, and the name of the field where the parent id should be stored. It pages through all parents, takes each parent’s id, fetches that parent’s child pages, and adds the parent id onto each child record if it is a dictionary-like record. It yields each child page after stamping those records.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is one of the known parent-child streams. This method relies on _paginate_link_header for both parent and child API calls, then passes the enriched child records back to paginate and onward to the sync system.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `source sync`

Recruitee is a recruiting tool, and this connector is the small adapter that lets this project pull data out of it in a predictable way. The file defines which Recruitee lists are available: candidates, offers, and departments. Each list is described as a stream, meaning a named flow of records with a stable identifier, such as an `id` field.

The connector is read-only. It does not create or update anything in Recruitee. Its job is like sending a clerk to collect pages from three filing cabinets, one page at a time, until there are no more full pages to collect.

Recruitee’s API returns data in numbered pages, with up to 100 records per page. The connector asks for `/candidates`, `/offers`, or `/departments`, then keeps asking for the next page until the shared REST helper decides the list is finished. The base web address is intentionally left empty here because it depends on the Recruitee company account. That full address must be supplied by the sync runner; this avoids accidentally calling the wrong tenant.

A notable behavior is how permission failures are treated. If Recruitee replies with 401 or 403, meaning the key is invalid or lacks permission, the stream is skipped with a clear message instead of crashing unclearly.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream in chunks, using Recruitee’s page-number style of browsing through results. It is used when the sync runner needs records for candidates, offers, or departments.

**Data flow**: It receives an already prepared web client, a stream description that says which Recruitee object to read, and an unused cursor value. It asks the shared REST connector helper for pages from the matching API path, expecting the records to be under a response key with the stream’s name. Each page of records is yielded back to the caller. If Recruitee refuses access with a 401 or 403 response, it changes that low-level web error into a clear `StreamSkipped` result explaining that the grant or key is not allowed.

**Call relations**: During a sync, the source framework calls this method for each declared Recruitee stream. The method delegates the repeated page fetching to the inherited REST pagination helper, so it does not have to build every URL itself. If Recruitee denies access, it creates a `StreamSkipped` exception to tell the wider sync flow to skip that stream for a known permission reason rather than treat it as an unknown failure.

*Call graph*: calls 1 internal fn (__init__).


### HR workforce sources
Connectors that read employee, contractor, company, team, payroll, and HR workflow data from workforce systems.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `source sync / API fetching`

BambooHR exposes company HR data through web API endpoints, but it does not behave like many APIs that return neatly paged results. Most BambooHR list endpoints return all matching records at once, and different endpoints wrap their results in different shapes. This file smooths out those differences so the rest of the sync system can ask for a named stream, such as employee directory or time-off requests, and receive batches of plain records.

The file first defines the BambooHR streams the connector can read: directory employees, detailed employee records, time-off requests, timesheet entries, metadata fields, and a custom employee report. The connector then builds an HTTP client with the right BambooHR rules: JSON must be requested explicitly, the tenant-specific base URL must already be known, and the API key is sent using HTTP Basic authentication with the key as the username and "x" as the password.

The main dispatch point is `paginate`. Despite the name, BambooHR does not use traditional page numbers here. Instead, `paginate` routes each stream name to the correct fetch routine. Some streams are simple one-call reads. Detailed employees are different: the connector first reads the directory, then asks BambooHR for each employee’s full record one by one. For time-based streams, it builds a start-and-end date window from the previous sync cursor. If BambooHR rejects access with 401 or 403, the stream is skipped with a clear message instead of crashing the whole connector.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: Creates a stream description for one kind of BambooHR data, such as employees or time-off requests. The rest of the connector uses these descriptions to know each stream’s name, source object, primary key, cursor field, and whether it is a canonical stream.

**Data flow**: It receives stream settings like a name, key field, and optional timestamp fields. It fills in sensible defaults, such as using the stream name as the source object when none is given, and returns a `StreamSpec`, which is the system’s compact description of a readable stream.

**Call relations**: This helper is used while the file is loaded to build `BAMBOOHR_STREAMS`. It hands those stream definitions to `BambooHRConnector`, which exposes them as the list of BambooHR streams the sync system can request.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to BambooHR. It makes sure requests use the right base URL, JSON headers, timeouts, and authentication style expected by BambooHR.

**Data flow**: It receives a BambooHR base URL and a resolved credential. It trims extra slashes from the URL, sets connection and read time limits, and adds headers asking BambooHR for JSON. If the credential already provides a custom transport, it uses that unchanged. If the credential carries an API key, it turns that key into BambooHR’s Basic authentication format. It returns an `httpx.AsyncClient`, which is an asynchronous web client.

**Call relations**: The broader REST connector machinery calls this when it is ready to open a connection for a sync. This function prepares the client that later fetch functions use to make BambooHR API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right BambooHR fetch routine for the requested stream and yields batches of records. It is the central traffic director for reading BambooHR data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching private fetch function, and passes each returned batch onward. If BambooHR refuses access with a 401 or 403 response, it turns that into a `StreamSkipped` error with an explanation; other HTTP errors are allowed to continue upward.

**Call relations**: The sync engine calls `paginate` when it wants records for a BambooHR stream. `paginate` then hands off to `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports` depending on the stream name.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches BambooHR’s employee directory, which is the broad list of employees returned by the directory endpoint. This gives the sync system the basic employee list in one batch.

**Data flow**: It receives an HTTP client, calls the BambooHR employee directory endpoint, and looks for records under the `employees` field. If records are present, it yields them as one list; if there are none, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. It is also conceptually the starting point for employee detail syncing, though the detail function performs its own directory read.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches full detail records for individual employees. It first gets the directory so it knows which employee IDs exist, then asks BambooHR for each employee’s detailed record.

**Data flow**: It receives an HTTP client. It reads the employee directory, loops through rows that look like dictionaries, extracts each employee ID, and requests `/v1/employees/{id}` for that employee. If BambooHR returns a detail object, it makes sure the `id` field is present and yields that single employee record as a one-item batch.

**Call relations**: `paginate` calls this for the `employees` stream. This function breaks the larger task into many small handoffs, yielding after each employee so the sync process can make progress and checkpoint frequently.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches time-off requests within a date window. The date window is needed because BambooHR expects `start` and `end` parameters for this endpoint.

**Data flow**: It receives an HTTP client and an optional cursor from the previous sync. It turns the cursor into BambooHR date parameters using `_date_window_params`, requests the time-off endpoint, and accepts either a raw list response or a response with records under `requests`. If any records are found, it yields them as one batch.

**Call relations**: `paginate` calls this when syncing `time_off_requests`. Before making the API request, it relies on `_date_window_params` to translate the sync cursor into the date format BambooHR expects.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches timesheet entry records within a date window. Like time-off requests, this endpoint needs explicit start and end dates.

**Data flow**: It receives an HTTP client and an optional cursor. It builds date parameters with `_date_window_params`, calls BambooHR’s timesheet entries endpoint, and reads records either from a raw list response or from an `entries` field. If records exist, it yields them as one batch.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. It shares the same date-window helper as `_fetch_time_off`, keeping the cursor-to-date behavior consistent for both time-based streams.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches BambooHR’s field catalog, which describes available employee fields. This helps the system learn what fields BambooHR knows about.

**Data flow**: It receives an HTTP client, requests `/v1/meta/fields`, and accepts either a raw list response or a response with records under `fields`. If records are present, it yields them as one batch.

**Call relations**: `paginate` calls this when the requested stream is `meta_fields`. It is one of the simple one-request streams in this connector.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs a BambooHR custom report with a fixed list of useful employee fields. This gathers a curated employee dataset in the report format BambooHR provides.

**Data flow**: It receives an HTTP client, builds a report request body with a title and selected fields such as name, email, job title, department, supervisor, and hire date, then posts it to `/v1/reports/custom`. It reads returned rows from the `employees` field and yields them as one batch if any are present.

**Call relations**: `paginate` calls this for the `custom_reports` stream. Unlike most other fetch routines here, it uses a POST-style report request because BambooHR custom reports are requested by sending a report definition.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: Turns a previous sync cursor into the `start` and `end` date parameters BambooHR requires for time-based endpoints. It gives fresh syncs a very early start date so they can collect all available history.

**Data flow**: It receives an optional cursor, usually a timestamp or date string. If the cursor has text, it takes the first 10 characters as a `YYYY-MM-DD` date; otherwise it uses `1970-01-01`. It always returns a dictionary with that `start` date and a far-future `end` date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this before making their API requests. It keeps both streams using the same rule for converting sync progress into BambooHR’s required date-window parameters.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `source sync / data fetching`

This connector is a read-only bridge between Deel and the wider UFO source system. Deel exposes its data through a REST API, which means the connector must ask for records over HTTP and follow Deel’s paging rules: request up to 100 items, then ask for the next 100 using an offset, and stop when fewer than 100 come back. Without this file, the system would not know which Deel objects to fetch, which API paths to use, or how to continue through multiple pages of results.

The file first defines a small helper for building stream descriptions. A stream is one kind of Deel data, like “contracts” or “timesheets.” Each stream says what it is called, what field uniquely identifies a record, and whether it can be updated incrementally. Incremental sync means “only fetch records changed since the last saved time.” For streams with an update time field, the connector sends Deel an `updated_after` filter. Forms do not have that cursor field here, so they are refreshed in full each run.

The `DeelConnector` then supplies the concrete behavior: the Deel API base address, the list of streams, the initial request parameters, the record extraction rules, and the pagination loop. Think of it like a librarian asking for boxes of files 100 at a time until the shelf is empty.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of Deel object. It keeps the stream list compact and consistent, so each Deel data type is described in the same way.

**Data flow**: It takes a stream name and optional details such as the API object name, primary key, cursor field, and whether it is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the shared source-system description of what to fetch and how to identify records.

**Call relations**: This helper is used while the file is loaded to build the `DEEL_STREAMS` list. It hands its gathered settings to `StreamSpec`, so the connector later has a clear catalog of Deel streams to page through.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the starting query parameters for a Deel API request. It always asks for records in batches of 100 and, when possible, adds a filter to fetch only records updated after the saved cursor.

**Data flow**: It receives a stream description and an optional cursor value, such as a previous update timestamp. It starts with `limit: 100`; if both a cursor and a cursor field exist for that stream, it adds `updated_after` with the cursor value. It returns the parameter dictionary that will be sent with API requests.

**Call relations**: `DeelConnector.paginate` calls this before it begins fetching pages. The returned parameters become the base request settings for every page of that stream, with `paginate` adding the changing `offset` value for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This pulls actual record dictionaries out of Deel API responses. It accepts both Deel’s usual wrapped shape, where records live under `data`, and a plain list shape, while ignoring anything that is not a record-like dictionary.

**Data flow**: It receives raw response data from the HTTP request. If the response is a dictionary with a list under `data`, it keeps only the dictionary items from that list. If the response itself is a list, it again keeps only dictionary items. If neither shape fits, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each API response arrives. The extracted records decide what gets yielded to the sync system and also tell the pagination loop whether it should continue or stop.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main loop that fetches one Deel stream page by page. It hides Deel’s offset-based pagination from the rest of the system and yields clean batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the API path for that stream, creates base query parameters, then repeatedly adds the current offset, makes a GET request, extracts records, and yields each non-empty batch. It stops when Deel returns no records or when the batch is smaller than 100, which means there are no more full pages to fetch.

**Call relations**: The broader source framework calls `paginate` when it wants records for a Deel stream. Inside the loop, `paginate` asks `_initial_params` for the stable request settings and `_extract_records` to turn each raw API response into usable records before yielding them onward.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `source sync / API pagination`

Rippling exposes business data through a web API, but it does not send everything in one response. It sends a page of rows, plus a “next” link when more pages are available. This file is the adapter that knows Rippling’s particular rules: which streams exist, where their records appear in a response, how to ask for only recently changed workers or teams, and how to keep following pages until there are no more.

The main class, `RipplingConnector`, is read-only. It does not write anything back to Rippling. Think of it like a librarian who knows which shelves exist and how to keep asking for the next box of index cards until the shipment is complete.

The connector defines three streams: companies, workers, and teams. Workers and teams support incremental syncing, meaning the system can ask for records updated after a saved timestamp. Companies do not have that cursor here, so they are fetched as a full refresh.

The important behavior is in `paginate`. It builds the first request, downloads a page, extracts only real record dictionaries, yields them as a batch, then follows Rippling’s `next` link. If Rippling rejects the request with 401 or 403, the connector turns that into `StreamSkipped`, which tells the wider system this stream cannot be read because the token is invalid or lacks permission.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s “next page” link into the path the connector should request next. It accepts both full web addresses and shorter relative paths, because APIs may return either shape.

**Data flow**: It receives a possible next-page link. If the link is missing, it returns nothing. If the link is a full URL, it parses it, keeps only the path and query string, and returns that smaller request path. If it is already a relative path, it returns it unchanged.

**Call relations**: `paginate` calls this after each downloaded page to decide whether there is another page to fetch. It uses URL parsing so the rest of the connector can keep making requests against the configured Rippling base URL instead of accidentally treating an absolute link as a separate target.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first request to Rippling. It sets the page size and, when possible, adds the saved timestamp used for incremental syncing.

**Data flow**: It receives a stream description and an optional cursor value, which is usually a timestamp from the previous sync. It always adds a `limit` of 100 records. If the stream supports a cursor and a cursor was provided, it adds `updatedAfter` so Rippling returns only newer or changed records. It returns the completed parameter dictionary.

**Call relations**: `paginate` calls this once before the first API request. After that first request, `paginate` clears the parameters because later pages are controlled by Rippling’s own `next` link.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls the actual list of records out of Rippling’s response body. It understands the few response shapes Rippling may use, and ignores anything that is not a record object.

**Data flow**: It receives decoded response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream name, such as `workers` or `teams`. If that is not present, it looks for a generic `data` list. If the whole response is already a list, it uses that. In every case, it keeps only dictionary items and returns them as records; if nothing matches, it returns an empty list.

**Call relations**: `paginate` calls this after each API response arrives. This keeps pagination focused on the page-by-page loop while this helper deals with the practical detail that Rippling may wrap records in different ways.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It requests pages from Rippling, yields batches of records, follows the next-page link, and reports permission problems in a way the sync runner understands.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It starts at the stream’s API path, builds the first query parameters, and repeatedly asks Rippling for data. For each response, it extracts records and yields them if any exist. Then it reads the response’s `next` value to decide the next path. If Rippling returns 401 or 403, it changes that web error into `StreamSkipped`; other HTTP errors are allowed to continue upward unchanged.

**Call relations**: The wider source-sync system calls `paginate` when it wants records for companies, workers, or teams. Inside the loop, `paginate` delegates small decisions to `_initial_query`, `_extract_records`, and `_next_path`. When access is refused, it creates a `StreamSkipped` error so the caller can skip that stream instead of treating the whole connector as a normal successful read.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).


### Spend and accounting sources
Connectors that sync spend management and accounting records from finance systems of record.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `syncing Brex source data`

This connector is the system’s read-only bridge to Brex, a business spending platform. Without it, the project would not know where Brex keeps its lists of transactions, expenses, vendors, and related records, or how to fetch more than the first page of results.

The file first describes the Brex “streams,” meaning the kinds of records that can be synced. Each stream says what it is called, which field identifies one record from another, and whether there is a date field that can act like a progress marker. For example, transactions use `posted_at_date`, and expenses use `purchased_at`. Most Brex endpoints do not let the connector ask for “only records updated since last time,” so the connector generally reads the full list and lets the wider system decide what is new.

The main class, `BrexConnector`, gives the shared REST connector framework the Brex base web address and the list of available streams. Its pagination method follows Brex’s standard pattern: ask for up to 100 records, read the `items` list, then use `next_cursor` as a ticket for the next page. It repeats until Brex stops giving a next cursor. Think of it like reading a long report where each page tells you the page number for the next sheet.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Brex data stream, such as `transactions` or `vendors`. It keeps the stream setup compact and consistent so the connector framework knows how each kind of Brex record should be identified and tracked.

**Data flow**: It receives a stream name and optional details like the primary key field, a cursor date field, and whether the stream is considered canonical. It puts those details into a `StreamSpec`, which is a small description object used by the source framework, and returns that object for inclusion in the Brex stream list.

**Call relations**: This helper is used while the file is loaded to build `BREX_STREAMS`, the catalog of Brex record types the connector can read. It hands each finished stream description to the shared source framework through `StreamSpec`.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads all pages for one Brex stream from the Brex API. Someone uses it when the sync process needs the actual records for a stream, not just the stream’s description.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor argument. It looks up the correct Brex API path for that stream, asks Brex for records in batches of 100, turns the returned `items` value into a safe list, yields each non-empty batch, then follows Brex’s `next_cursor` value to request the next batch. When there is no next cursor, it stops. If the stream has no known Brex endpoint, it raises an error instead of guessing.

**Call relations**: The shared REST source machinery calls this method during a Brex sync whenever it needs records for a stream. Inside the loop, this method relies on the parent connector’s HTTP GET helper to fetch data from Brex, and it uses `ufo.sdk.sources.list_or_empty` to make sure the `items` field can be safely treated as a list before handing batches back to the sync flow.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `during source sync, when reading QuickBooks streams`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each kind of record. Instead, every read is sent as a SQL-like query to one shared API path. This file hides that awkward detail from the rest of the system.

It starts by listing the QuickBooks record types the connector can read, such as accounts, customers, invoices, bills, payments, journal entries, and tax codes. Each record type is described as a stream: a repeatable feed of records with a primary key, usually `Id`, and often a cursor field. A cursor is like a bookmark that says, “next time, only fetch records updated after this time.” For QuickBooks, that bookmark lives inside the record at `MetaData.LastUpdatedTime`.

`QuickBooksConnector` then knows how to turn each stream into QuickBooks’ query language, fetch results from `/query`, and move through pages of up to 100 records. If QuickBooks says access is forbidden or unauthorized, the connector marks that stream as skipped rather than crashing the whole sync. Finally, because the cursor is nested inside each record, the connector can copy it onto a flat field name so the wider sync system can easily track progress.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description for one QuickBooks stream, such as invoices or customers. It keeps the long stream list readable by filling in shared QuickBooks rules, like using `Id` as the primary key and `MetaData.LastUpdatedTime` as the usual update bookmark.

**Data flow**: It receives a friendly stream name, the matching QuickBooks object name, and optional settings such as whether the stream has a cursor. It packages those details into a `StreamSpec`, which is the system’s standard description of a readable data feed. The result is used later by the connector when deciding what to request and how to track progress.

**Call relations**: This function is used while building the `QUICKBOOKS_STREAMS` list at import time. It hands each finished stream description to the connector class, which later uses those descriptions during syncing.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This method builds the SQL-like text query that QuickBooks expects. It decides which QuickBooks object to read, whether to include a “changed after this cursor” filter, and which page of results to ask for.

**Data flow**: It receives a stream description, an optional cursor value, and the starting row number for the page. It creates a query string such as “select all invoices updated after this time, ordered by update time, starting at this position, with at most 100 results.” If the cursor contains an apostrophe, it escapes it so the query remains valid.

**Call relations**: The pagination loop calls this method before every API request. It hands the finished query back to `paginate`, which sends it to QuickBooks’ `/query` endpoint.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one QuickBooks stream in pages. It keeps asking QuickBooks for the next batch until QuickBooks returns fewer than 100 records, which means there are no more pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It repeatedly builds a QuickBooks query, sends it to the API, pulls the records out of `QueryResponse`, and yields each non-empty batch to the caller. It updates the start position after each full page. If QuickBooks returns 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` so this one stream can be skipped with a clear reason.

**Call relations**: This is the main read loop used by the broader REST connector machinery during a sync. It calls `_build_query` to prepare each request, uses the inherited `_get` request helper to contact QuickBooks, and reports access refusals through `StreamSkipped` so the sync runner can react cleanly.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method makes QuickBooks records easier for the sync system to bookmark. When the cursor is nested inside `MetaData.LastUpdatedTime`, it copies that value onto a top-level field with the same dotted name.

**Data flow**: It receives one raw QuickBooks record and the stream description. If the stream’s cursor field is a nested path, it reads that nested value using `get_path` and returns a new record that includes the copied flat cursor field. If no nested cursor is needed, it returns the record unchanged.

**Call relations**: The wider connector flow calls this after records are fetched and before progress is tracked. It relies on `get_path` to read nested data safely, and it gives the sync adapter a flat cursor value it can compare and store.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `active during source sync when fetching Xero streams`

Xero is an accounting service, and its API has a few habits that the rest of the system should not need to know about. This file hides those habits behind a XeroConnector. It defines the list of Xero resources that can be synced, gives each one a stable stream name, and says which field should be used as its record ID and update timestamp.

The connector only reads data. It does not create or edit anything in Xero. During a sync, it asks Xero for one stream at a time. Most Xero resources come back in pages of up to 100 records, so the connector keeps asking for page 1, page 2, and so on until it gets a short or empty page. Some smaller resources do not really support paging, so those are fetched once.

For incremental sync, meaning “only give me records changed since last time,” Xero expects a special HTTP header called If-Modified-Since rather than a normal URL parameter. The file converts saved cursor values into the date format Xero expects. It also adds the Xero tenant ID header when needed, because one login can grant access to more than one Xero organisation.

Finally, Xero names ID fields differently for each resource, like InvoiceID or AccountID. The flatten step copies that typed ID into a plain id field, so downstream code can treat every stream consistently.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the standard description for one Xero stream, such as accounts or invoices. It keeps the stream list short and consistent, so every stream gets the same basic settings unless it needs an exception.

**Data flow**: It receives a friendly stream name, the exact Xero response envelope name, an optional cursor field, and a flag saying whether the stream is a core one. It packages those details into a StreamSpec with a shared primary key named id and an update field named UpdatedDateUTC. The result is a stream definition used later by the connector during sync.

**Call relations**: This helper is used while the file builds XERO_STREAMS at import time. It hands each finished StreamSpec to the connector through the streams_list class setting, so the broader sync system knows which Xero resources are available.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved sync cursor into the date text format that Xero expects in the If-Modified-Since request header. In plain terms, it translates “where we left off” into Xero’s preferred calendar wording.

**Data flow**: It receives a cursor value that may be empty, a Unix timestamp made of digits, an ISO-style date string, or some other text. Empty or invalid timestamp values become no header value. Valid timestamps and date strings are converted to UTC and formatted like an HTTP date ending in GMT. If the value is not recognized as an ISO date, the original text is returned so it can still be sent along.

**Call relations**: XeroConnector.paginate calls this when it is about to fetch a stream that supports incremental updates. The returned string is placed into the If-Modified-Since header, which tells Xero to send only records changed after that time.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This sets up a Xero connector instance with an optional tenant ID. A tenant ID identifies the specific Xero organisation to read from when the same authorization could cover more than one organisation.

**Data flow**: It receives an optional tenant ID string. It stores that value on the connector for later. It does not contact Xero or return data; it only prepares the connector’s state.

**Call relations**: Code that creates a XeroConnector calls this before syncing starts. Later, _make_client reads the stored tenant ID and adds it to outgoing API requests when needed.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Xero and adds Xero’s required tenant header when a tenant ID was provided. The HTTP client is the object that actually sends web requests.

**Data flow**: It receives the API base URL and a credential object supplied by the system’s authentication proxy. It first asks the parent REST connector to build the normal authorized client. If this connector has a tenant ID, it adds xero-tenant-id to the client’s default headers. It returns the prepared client.

**Call relations**: The sync framework calls this when it needs a client for Xero requests. It relies on the base RestConnector for the common authentication setup, then adds the Xero-specific tenant detail before paginate starts making GET requests.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches records for one Xero stream, yielding them in batches. It knows Xero’s paging rules, its one-shot streams, and its special incremental-sync header.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from a previous sync. It builds the Xero path from the stream’s source object. If the stream can use a cursor, it converts that cursor into an If-Modified-Since header. For non-paged streams, it makes one GET request and yields the records inside Xero’s named envelope. For paged streams, it requests page after page, yielding each non-empty list, and stops when Xero returns fewer than 100 records or no records. If Xero replies with 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped so this stream can be skipped with a clear explanation; other HTTP errors are allowed to bubble up.

**Call relations**: The broader sync process calls this whenever it needs data from a particular Xero stream. Inside the function, it calls _cursor_to_rfc1123 to prepare incremental headers and uses the httpx client to send the actual web requests. When access is refused, it hands control back to the sync system by raising StreamSkipped instead of pretending the stream is empty.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Xero records so each one has a plain id field. That matters because Xero uses different ID field names for different resources, while the rest of the sync system expects one common primary key name.

**Data flow**: It receives one record and the stream it came from. If the record already has id, it returns the record unchanged. Otherwise, it looks up the Xero-specific ID field for that stream, such as InvoiceID or ContactID. If that field exists in the record, it returns a copy of the record with id added as a string. If no suitable ID is found, it returns the original record unchanged.

**Call relations**: After paginate has fetched raw records from Xero, the sync framework can call this to shape each record for storage or downstream processing. It does not call other project code; it uses the file’s ID-field map to bridge Xero’s naming style with the system’s standard record format.


### Subscription billing sources
Connectors that read recurring billing, subscription, invoice, customer, and charge records from subscription platforms.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `sync run, while reading Chargebee streams`

Chargebee is a billing service, and this connector is the read-only bridge from Chargebee into UFO’s sync system. Without it, the system would not know how to fetch customers, subscriptions, invoices, transactions, items, quotes, and related records from a Chargebee tenant.

The file starts by defining the Chargebee streams: each stream is one kind of thing that can be copied, such as a customer or invoice. It also records which field should be used as a cursor, meaning the “last seen” value used to fetch only newer records on the next run.

Chargebee’s API returns results in pages. Each page contains a list of wrapped records and, sometimes, a next_offset token that means “ask for the next page using this token.” The connector follows those tokens until there are no more pages, like turning pages in a book until the bookmark disappears.

Most streams use a simple list endpoint. A few are substreams: for example, contacts are fetched by first listing customers, then asking Chargebee for each customer’s contacts. Those child records are stamped with the parent ID so they can still be tied back to the customer, item, quote, or subscription they came from.

Authentication uses Chargebee’s API key through HTTP Basic authentication. If Chargebee rejects a stream with 401 or 403, the connector reports that stream as skipped instead of pretending the sync succeeded.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a small description of one Chargebee stream, such as customers or invoices. This description tells the rest of the sync system what the stream is called, what its unique ID field is, and which time field can be used for incremental syncing.

**Data flow**: It receives stream settings such as the stream name, source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults where values are not supplied, then returns a StreamSpec object that the connector can later use to decide how to read that stream.

**Call relations**: This helper is used while the file is loaded to build the CHARGEBEE_STREAMS list. It hands each stream definition to StreamSpec so the wider source framework has a uniform description of all Chargebee record types.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Chargebee. It sets time limits, default headers, the tenant-specific base address, and the right authentication method.

**Data flow**: It receives a base URL and a resolved credential. It trims the base URL, creates timeout and header settings, then either uses a supplied transport from a credential broker or creates Basic authentication from a direct API key. It returns an httpx AsyncClient ready to make Chargebee API requests, or raises an error if no usable authentication is present.

**Call relations**: The source framework calls this when it is preparing to sync Chargebee. The returned client is then passed into pagination methods such as ChargebeeConnector.paginate and the lower-level page readers.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns Chargebee’s wrapped records into simpler records. Chargebee often returns a record like {"customer": {...}}; this function lifts the inner customer fields up so downstream code can read the record directly.

**Data flow**: It receives one raw record and the stream description that says which wrapper key to expect. If the expected wrapper contains a dictionary, it copies the inner data and also keeps extra top-level values, such as a parent ID added by a substream. It returns the flattened record. If the expected wrapper is not present, it returns the original record unchanged.

**Call relations**: This fits after pages have been fetched from Chargebee and before records are stored or processed by the rest of the sync system. It does not call other connector methods; it is a cleanup step that makes Chargebee’s response shape match UFO’s simpler record shape.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to fetch pages for a requested Chargebee stream. Most streams use a normal list endpoint, while special child streams need to loop through parent records first.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It checks the stream name, delegates to the matching pagination method, and yields each page of records as it arrives. If Chargebee returns a 401 or 403 refusal, it converts that into a StreamSkipped error with a clear message; other HTTP errors continue upward.

**Call relations**: The sync framework calls this when it wants records for a stream. This method is the traffic director: it sends attached items, contacts, quote line groups, and scheduled subscription changes to their special routines, sends ordinary streams to ChargebeeConnector._paginate_list, and raises an explicit error if no strategy exists.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for a normal Chargebee list request. It always asks for a fixed page size and, when possible, adds an incremental “after this cursor” filter.

**Data flow**: It receives a stream description and an optional cursor value. It starts with a limit of 100 records. If both a cursor value and a cursor field exist, it adds a Chargebee-style parameter such as updated_at[after]=value. It returns the completed parameter dictionary.

**Call relations**: ChargebeeConnector._paginate_list calls this before making list requests. It keeps the pagination code focused on walking pages while this helper decides what to put in the request.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pages from one of Chargebee’s standard list endpoints, such as /customers or /invoices. It follows Chargebee’s next_offset token until there are no more pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the API path for the stream, builds the request parameters, then asks the shared REST paging helper to fetch records from the response’s list field and follow next_offset. It yields each page of raw Chargebee records.

**Call relations**: ChargebeeConnector.paginate uses this for ordinary streams. The substream methods also use it first to fetch parent records, such as customers before contacts or items before attached items.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches attached items, which Chargebee exposes under each individual item rather than as one simple global list. It first finds items, then asks for the attached items belonging to each one.

**Data flow**: It receives an HTTP client and an optional cursor. It pages through the item stream, extracts each item ID, skips parents without an ID, then calls the generic substream paginator for /items/{item_id}/attached_items. Each yielded child page includes the item_id so the attached item can be linked back to its item.

**Call relations**: ChargebeeConnector.paginate calls this when the requested stream is attached_item. This method relies on ChargebeeConnector._paginate_list to get parent items and ChargebeeConnector._paginate_substream to read each item’s child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches customer contacts, which are stored under individual customers in Chargebee. It walks through customers first, then retrieves each customer’s contacts.

**Data flow**: It receives an HTTP client and an optional cursor. It pages through customers, extracts the customer ID from each parent record, skips records without an ID, and then reads /customers/{customer_id}/contacts. The child contact records are yielded with customer_id added so they remain connected to their customer.

**Call relations**: ChargebeeConnector.paginate calls this for the contact stream. It uses ChargebeeConnector._paginate_list for the parent customer pages and ChargebeeConnector._paginate_substream for the repeated child-list calls.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches quote line groups, which Chargebee provides under each quote. It first reads quotes, then retrieves the line groups for each quote.

**Data flow**: It receives an HTTP client and an optional cursor. It lists quote records, extracts each quote ID, skips any quote without an ID, and calls the substream paginator for /quotes/{quote_id}/quote_line_groups. It yields child pages with quote_id stamped onto each record.

**Call relations**: ChargebeeConnector.paginate uses this when syncing quote_line_group. Like the other substream fetchers, it combines ChargebeeConnector._paginate_list for parents with ChargebeeConnector._paginate_substream for children.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the special “subscription with scheduled changes” view for each subscription. This is not a normal list of child records; it is a detail lookup done once per subscription.

**Data flow**: It receives an HTTP client and an optional cursor. It pages through subscriptions, extracts each subscription ID, and for each valid ID requests /subscriptions/{id}/retrieve_with_scheduled_changes. If Chargebee returns a subscription object, it yields a one-record page containing that subscription and the subscription_id used to fetch it.

**Call relations**: ChargebeeConnector.paginate calls this for the subscription_with_scheduled_changes stream. It uses ChargebeeConnector._paginate_list to find the parent subscriptions, then performs one direct fetch per subscription for the scheduled-change detail.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the shared page-reading loop for child lists that live under a parent record. It also adds the parent ID to each child record so the relationship is not lost.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent ID field to add, and the parent ID value. It fetches pages from the child endpoint using Chargebee’s list and next_offset format. For each dictionary record, it copies the record, adds the parent ID field, groups the enriched records into pages, and yields only non-empty pages.

**Call relations**: The attached item, contact, and quote line group paginators call this after they have found a parent ID. It centralizes the repeated work of walking a child endpoint and stamping each child with its parent.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during source sync`

Recurly’s API does not return everything at once. It gives a page of records, plus a “next” link if more records are waiting. This file is the connector that turns those pages into streams the rest of the system can sync, much like a librarian repeatedly asking for the next cart of books until the shelf is empty.

The file defines the list of Recurly streams, such as accounts, subscriptions, invoices, coupons, and related child records. Some records live directly at a top-level API path, like `/accounts`. Others live underneath a parent, such as notes under each account. For those child streams, the connector first walks through all parent records, then asks Recurly for each parent’s children, and adds the parent id onto each child row so the relationship is not lost.

Authentication is also handled here. Recurly expects HTTP Basic authentication, using the API key as the username. If the system is using an auth proxy, the connector uses the proxy transport instead. The connector also pins the Recurly API version through an HTTP header, which helps keep responses stable over time.

If Recurly refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of treating it like an ordinary data page. This usually means the key is invalid or lacks permission for that area.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of Recurly data, such as accounts or invoices. It keeps the stream list readable by filling in common defaults like the primary key and cursor field.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, and whether it is a main canonical stream. It packages those choices into a StreamSpec object, which the connector framework later uses to know what to sync and how to track progress.

**Call relations**: This helper is used while the file is loaded to build the Recurly stream list. It hands each finished stream description to StreamSpec so the rest of the connector can work from consistent metadata.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It also applies the required API version header and chooses the right authentication method.

**Data flow**: It receives a base URL and a credential. It trims the base URL, prepares timeout settings and headers, then either uses an auth-proxy transport or creates Basic authentication from the API key. The result is an asynchronous HTTP client ready to make Recurly requests; if no usable credential is present, it raises an error.

**Call relations**: The connector framework relies on this when it needs a network client for Recurly. Internally it delegates the low-level client, timeout, and Basic authentication setup to httpx, the HTTP library.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This converts Recurly’s “next page” link into a path the existing client can request. It accepts both full URLs and already-relative paths.

**Data flow**: It receives a possible next-link string. If there is no link, it returns nothing. If the link is a full URL, it strips it down to just the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: The paging functions call this after each Recurly response to decide where to ask next. It uses Python’s URL parser to safely separate a full URL into the pieces the client needs.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the first set of query parameters for a Recurly list request. It sets the page size, sort order, and optional incremental starting point.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It builds parameters that ask Recurly for up to 200 records in ascending order, sorted by the stream’s cursor field when available. If a cursor is present, it adds it as `begin_time` so Recurly returns records from that point forward.

**Call relations**: Both the top-level and per-parent pagination flows call this before their first request. After the first page, Recurly’s own next link carries the paging position, so this initial query is not reused.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for a Recurly stream. Given one stream and an optional saved cursor, it yields pages of records until that stream is exhausted or skipped.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It chooses the correct reading path: child records under parents, bulk coupon parents, or an ordinary top-level endpoint. It yields lists of record dictionaries as they arrive. If Recurly responds with an authorization refusal, it turns that into a StreamSkipped signal with a clear message.

**Call relations**: This is the function the broader sync flow calls when it wants records from a specific Recurly stream. It hands the actual page walking to either _paginate_per_parent or _paginate_top_level, then passes each resulting page back up to the caller.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through a normal Recurly list endpoint, such as accounts or invoices. It keeps following Recurly’s next-page pointer until there are no more pages.

**Data flow**: It receives an HTTP client, a stream description, an API path, and an optional cursor. It builds the first query, requests a page, yields any records found, then uses the response’s `next` link if `has_more` is true. When Recurly says there are no more pages, it stops.

**Call relations**: paginate calls this for streams that live directly at their own endpoint, and also for the special coupon-parent stream. It relies on _initial_query for the first request and _next_path for each follow-up link.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This reads all account ids from Recurly so child streams can be fetched under each account. It is a preparatory step for account-based subresources like notes or billing information.

**Data flow**: It starts at the accounts endpoint with a basic sorted query. For each page, it looks through the returned rows and yields the id from each account that has one. If Recurly reports more pages, it follows the next link; otherwise it stops.

**Call relations**: _paginate_per_parent calls this when it needs account ids before fetching child records. This function uses _next_path to continue through Recurly’s paged account list.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This reads coupon ids, but only for bulk coupons. Those ids are needed before fetching the unique coupon codes that belong under each bulk coupon.

**Data flow**: It starts at the coupons endpoint and requests coupons in creation order. For every returned row, it checks that the row is a dictionary, has an id, and has `coupon_type` equal to `bulk`. It yields only those matching ids, then follows next-page links until Recurly has no more coupon pages.

**Call relations**: _paginate_per_parent calls this for coupon-based child streams. Like the account id reader, it uses _next_path to keep moving through Recurly’s paginated responses.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that are nested under parent records, such as account notes under accounts or unique coupon codes under coupons. It preserves the parent-child connection by adding the parent id to each returned child row.

**Data flow**: It receives the parent endpoint, child endpoint name, field name to stamp, stream description, client, and optional cursor. It first chooses whether to list account ids or bulk coupon ids. For each parent id, it requests that parent’s child endpoint, yields each non-empty page of child records, and adds the parent id into each child dictionary when possible. It follows child-page next links until that parent is done, then moves to the next parent.

**Call relations**: paginate calls this for streams listed as per-parent streams. It depends on _account_ids or _coupon_ids to find the parents, uses _initial_query for each child collection’s first request, and uses _next_path whenever Recurly points to another child page.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `source sync, while fetching Stripe records`

Stripe exposes business data through a web API, but it does not send everything in one simple list. Each list comes in pages, some data lives under a parent object, and some streams need special filters. This file is the adapter that knows those Stripe rules.

At the top, it defines the Stripe streams the system can read. A stream is a named collection of records, like “customers” or “invoices,” with hints such as its main ID field and the time field used for incremental syncing. Incremental syncing means asking only for records newer than a remembered point, instead of rereading everything.

The StripeConnector then does the actual reading. For ordinary streams, it calls Stripe’s list endpoint, asks for up to 100 records at a time, and follows Stripe’s “starting_after” pointer until there are no more pages. For child streams, like invoice line items, it first reads the parent invoices and then asks Stripe for each invoice’s children. It stamps the parent ID onto each child row so the relationship is not lost.

It also pins a Stripe API version in the request header, converts numeric Stripe timestamps into readable date strings, and treats 401 or 403 responses as a skipped stream rather than a full crash. The connector never stores the secret key itself; credentials come from the surrounding authentication proxy.

#### Function details

##### `_stream`  (lines 86–104)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates a stream description for one kind of Stripe record. It keeps the long stream list readable by filling in common defaults, such as using “id” as the main key and “created” as the usual time cursor.

**Data flow**: It receives a stream name and optional details, such as the Stripe API object name or cursor field. It combines those with sensible defaults and returns a StreamSpec, which is the system’s small instruction card for how to read that stream.

**Call relations**: This function is used while building the STRIPE_STREAMS list in this file. It hands the resulting StreamSpec objects to the StripeConnector class through its streams_list, so later sync code knows which Stripe collections exist.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 179–182)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the web client used to talk to Stripe. Its special job is to add the pinned Stripe API version header, so Stripe replies using the response shape this connector expects.

**Data flow**: It receives a base URL and a credential supplied by the surrounding system. It asks the parent RestConnector to create the actual asynchronous HTTP client, then adds the Stripe-Version header and returns the ready-to-use client.

**Call relations**: The broader connector framework calls this when it needs a Stripe HTTP client. After this setup, the rest of the pagination functions can use the client without repeatedly adding the version header.


##### `StripeConnector._list_path`  (lines 185–186)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This turns a stream description into the Stripe API path for listing that kind of object. For example, a stream whose source object is “customers” becomes “/v1/customers.”

**Data flow**: It receives a StreamSpec and reads its source_object value. It formats that value into a Stripe versioned API path string and returns the path.

**Call relations**: The main paginate method and the child-stream paginators call this whenever they need the list endpoint for a parent or ordinary stream. The returned path is then passed into _page_loop, which does the repeated API requests.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 189–201)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved sync cursor into the Unix timestamp format Stripe expects for date filters. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date-time string. It returns an integer timestamp when it can understand the input, or None when there is no usable cursor.

**Call relations**: _page_loop calls this before making API requests. If it gets a timestamp back and the stream uses Stripe’s “created” field as its cursor, _page_loop adds it to the request so Stripe only returns newer records.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 203–229)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading any Stripe stream. It decides which paging strategy fits the stream: ordinary list paging, child records under parent records, query-based child records, or external account records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It chooses the correct helper, yields pages of normalized record dictionaries, and turns Stripe 401 or 403 refusals into StreamSkipped so the sync can skip inaccessible streams cleanly.

**Call relations**: The connector framework calls paginate when it wants records for one stream. paginate routes the work to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, or _page_loop, and uses _list_path for ordinary streams.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 231–262)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable paging engine for Stripe list endpoints. It repeatedly asks Stripe for the next page until Stripe says there are no more records.

**Data flow**: It receives a client, an API path, a stream description, an optional cursor, and optional extra query parameters. It builds request parameters such as page size, starting_after, created[gte], and stream-specific filters; calls Stripe; normalizes each record through _browse_record; yields non-empty pages; and updates its position using the last record’s id.

**Call relations**: paginate uses this directly for simple streams, while the three specialized paginators use it for parent and child lists. It calls _cursor_to_unix to prepare incremental date filters and _browse_record to clean up each returned record.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._browse_record`  (lines 265–285)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly normalizes one Stripe record before the rest of the system sees it. Its main job is to turn Stripe’s numeric timestamps into readable ISO date-time strings in created_at and updated_at fields when possible.

**Data flow**: It receives one record dictionary and the stream it belongs to. It copies the record, converts numeric created_at or updated_at values if present, fills created_at from created when needed, fills updated_at from the stream’s cursor value when possible, and returns the copied normalized record.

**Call relations**: _page_loop calls this for every record returned by Stripe. That means all ordinary and child-stream pagination paths benefit from the same timestamp cleanup before pages are yielded.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 287–310)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that live under a parent object in the URL. For example, it can read line items under each checkout session or balance transactions under each customer.

**Data flow**: It looks up the parent stream, builds the parent list path, and pages through all parents. For each parent with an id, it formats the child URL, pages through the child records, and adds useful parent information such as the parent id or parent creation time to each child row before yielding it.

**Call relations**: paginate calls this when the stream name is listed in the child-path mapping. This helper depends on _stream_spec to find the parent StreamSpec, _list_path to build the parent endpoint, and _page_loop to fetch both parent and child pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_substream_query`  (lines 312–326)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child-like Stripe data where the child endpoint is not nested in the URL, but is filtered by a parent ID query parameter. It preserves the link back to the parent by adding that parent ID to each returned row.

**Data flow**: It finds the parent stream name, query field name, and child endpoint for the requested stream. It pages through parent records, skips parents without ids, calls the child endpoint with the parent id as a filter, and yields child rows with an added “<query_field>_id” value.

**Call relations**: paginate calls this for streams listed in the query-parent mapping, such as subscription items or setup attempts. It uses _stream_spec, _list_path, and _page_loop in the same parent-then-child pattern as _paginate_substream, but passes the parent id as an extra query parameter.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 328–341)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe accounts. These records require first walking through Stripe accounts, then asking for each account’s external accounts.

**Data flow**: It finds the accounts stream, pages through account records, skips any account without an id, requests that account’s external_accounts endpoint, and yields each child row with the account_id added so the source account is clear.

**Call relations**: paginate calls this for the external_account_bank_accounts and external_account_cards streams. It uses _stream_spec to find the accounts stream, _list_path to create the accounts endpoint, and _page_loop to fetch both accounts and their external account records.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 343–344)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the StreamSpec object for a named Stripe stream. It is a small lookup helper used when a child stream needs to know how to read its parent stream.

**Data flow**: It receives a stream name, searches the STRIPE_STREAMS list for a matching name, and returns the matching StreamSpec. If no match exists, the normal Python lookup behavior raises an error because the expected stream definition is missing.

**Call relations**: The specialized pagination helpers call this before reading parent streams such as accounts, customers, invoices, or subscriptions. The returned StreamSpec is then passed to _list_path and _page_loop so parent records can be fetched.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Commerce payment sources
Connectors that sync commerce and point-of-sale data such as customers, payments, orders, catalog items, and inventory.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `source sync`

Square exposes different kinds of data in different ways. Some data is fetched with simple list requests, some requires search requests sent with a JSON body, and some depends on first looking up the account’s locations. This file hides those differences behind one connector, so the rest of the project can ask for a named stream and receive batches of records.

The file defines the Square streams the system knows about, including their names, main ID fields, and cursor fields. A cursor is a saved point in time or position that lets a later sync continue from where an earlier one stopped, instead of reading everything again.

The main class, `SquareConnector`, is a read-only connector. It creates an HTTP client for Square, adds the required Square API version header, and then routes each requested stream to the right fetching method. For example, customers use cursor-based GET pages, catalog records use Square’s catalog search endpoint, and orders are searched across all Square locations.

If Square refuses access with a 401 or 403 response, the connector skips that stream with a clear message. This matters because a Square account may not grant every permission. Without this file, the system would not know how to reliably page through Square data or recover cleanly when a token lacks access.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Square. It adds Square’s required API version header so Square knows which version of its behavior and response formats the connector expects.

**Data flow**: It receives the Square base URL and a credential. It first lets the shared REST connector build the basic authenticated client, then adds the `Square-Version` header. It returns that ready-to-use client, with authentication and versioning in place.

**Call relations**: This is part of the connector setup before any stream is read. Later methods use the client it returns when they make GET and POST requests to Square.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the central router for reading Square streams. Given a stream name, it chooses the correct Square API pattern and yields records in pages, so the rest of the sync system does not need to know Square’s endpoint details.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and yields each non-empty page of records. If the stream is unknown, or Square refuses access with a 401 or 403 response, it raises `StreamSkipped`, which tells the wider system to skip that stream instead of crashing the whole sync.

**Call relations**: The sync runner calls this when it wants records for a Square stream. `paginate` then delegates to `_locations`, `_cursor_get`, `_catalog`, or `_orders` depending on the stream. For inventory counts, it makes the request directly and uses `records_at` to pull the list of count records out of Square’s response.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use ordinary cursor-based list endpoints, such as customers, payments, and refunds. It supports incremental syncing, meaning it can avoid returning records older than the saved cursor.

**Data flow**: It receives the HTTP client, a stream description, and an optional cursor. For payments and refunds, it sends the cursor to Square as a `begin_time` filter. For other cursor-based streams, it fetches pages and then locally keeps only records whose cursor field is newer than the saved cursor. It yields each filtered page that still contains records.

**Call relations**: `paginate` calls this for customers, payments, and refunds. This helper relies on the shared REST connector’s page-walking behavior to follow Square’s next-page cursor, then hands cleaned pages back to `paginate`.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either item records or category records. Square exposes these through the same search endpoint, so this function supplies the correct object type and walks through all result pages.

**Data flow**: It receives the HTTP client, the catalog stream description, and an optional saved cursor. It builds a search body with the wanted catalog object type and a page limit, sends it to Square, extracts the returned objects, and filters out records that are not newer than the saved cursor when needed. It follows Square’s returned cursor until there are no more pages, yielding non-empty batches along the way.

**Call relations**: `paginate` calls this when the requested stream is `catalog_items` or `catalog_categories`. Inside the loop it uses `records_at` to pull the `objects` list from Square’s response before passing those records back to the caller.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square locations for the account. Locations are useful as their own stream, and they are also needed before searching orders because Square order searches must name the locations to search.

**Data flow**: It receives the HTTP client, sends a GET request to Square’s locations endpoint, and reads the `locations` list from the response. It returns that list as plain record dictionaries.

**Call relations**: `paginate` calls this directly when syncing the `locations` stream. `_orders` also calls it first so it can collect the location IDs needed for the order search request.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all available account locations. It first discovers the locations, then searches orders for those locations page by page.

**Data flow**: It receives the HTTP client and an optional cursor. It fetches locations, keeps only valid string location IDs, and stops early if there are none. It then sends order search requests with those location IDs, adds a start-time filter when a cursor is present, follows Square’s returned page cursor, and yields each non-empty page of orders.

**Call relations**: `paginate` calls this for the `orders` stream. This function depends on `_locations` to know where to search, and it uses `records_at` to extract the `orders` list from each Square response before yielding it back into the sync flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).

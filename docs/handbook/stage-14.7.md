# HR and recruiting connectors  `stage-14.7`

This stage is the set of adapters that let the system bring in people and hiring data from outside services. It is shared support for the sync process: when the main system needs records, these files know how to talk to each provider’s web API, which is a service’s online doorway for requesting data. Each connector turns provider-specific pages of results into standard “streams,” meaning named flows of records that the rest of the system can process in the same way.

Ashby, Greenhouse, and Recruitee cover recruiting. They fetch items such as candidates, jobs, applications, interviews, offers, departments, and activity details. BambooHR focuses on employee operations, including employee records, time off, timesheets, metadata, and custom reports. Deel reads contractor and HR records such as contracts, forms, payslips, tasks, and timesheets. Rippling brings in company, worker, and team data. Together, these connectors act like translators at the front desk, each speaking one vendor’s language and handing clean batches to the common sync machinery.

## Files in this stage

### Recruiting platforms
Connectors that sync candidate, job, application, interview, offer, and department data from applicant tracking systems.

### `extensions/sources/ufo_ext_sources/providers/ashby.py`

`io_transport` · `data sync`

Ashby is a recruiting platform, and its API gives data back in pages rather than all at once. This file is the adapter that knows Ashby’s rules: which endpoints exist, how to authenticate, how to ask for the next page, and how to do incremental reads using Ashby’s sync token. Without it, the system would not know how to fetch Ashby records safely or consistently.

The file first defines a helper for building stream descriptions. A stream is a named kind of data, like “candidates” or “jobs”, with details such as its API path, main ID field, and update timestamp field. The `ASHBY_STREAMS` list then declares all the Ashby data collections this connector can read.

`AshbyConnector` is the main connector class. It uses HTTP Basic authentication in Ashby’s special form: the API key is used as the username, with an empty password. If credentials are being proxied by an auth broker, it leaves that transport untouched.

For most streams, pagination is straightforward: POST to the stream’s `.list` endpoint, send a page size, optionally send a stored sync token, and keep following Ashby’s cursor until there is no more data. One stream is different: criteria evaluations are not listed globally. The connector first lists applications, then asks Ashby for evaluations for each application, like checking each folder inside a filing cabinet.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for an Ashby API endpoint. It saves the repetitive details needed to describe each kind of Ashby data, such as its name, API path, ID field, and timestamp fields.

**Data flow**: It receives a friendly stream name and settings like the endpoint path, primary key, cursor field, and whether the stream is canonical. It uses those values to build a `StreamSpec`, which is the source framework’s small instruction card for how to read that stream. The result is returned and later collected into the Ashby stream catalog.

**Call relations**: This function is used while the file is loaded to build `ASHBY_STREAMS`. Each call produces one stream definition that `AshbyConnector` later exposes through its `streams_list` so the wider source system knows what Ashby collections are available.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client that will talk to Ashby’s API. Its main job is to turn the stored API key into the Basic Authorization header Ashby expects.

**Data flow**: It receives a base URL and a credential. If the credential already includes a special transport, such as one supplied by an authentication proxy, it lets the parent connector build the client without changing it. Otherwise it reads the API key from the credential, encodes `key:` as a Basic authentication token, builds a new credential containing the Authorization header, and returns an async HTTP client ready to send requests.

**Call relations**: The source framework calls this when setting up the connector’s network client. This method either hands off directly to the parent client builder for proxied credentials, or prepares Ashby’s required authorization header before handing off to that same parent builder.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This chooses the right paging strategy for a stream and yields batches of Ashby records. Most streams use the normal Ashby list pattern, but criteria evaluations need a special per-application lookup.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It checks the stream name. For `application_criteria_evaluations`, it delegates to the special fan-out reader; for every other stream, it delegates to the standard paged reader. In both cases, it yields lists of records as they arrive.

**Call relations**: The source framework calls this when it wants records from a particular Ashby stream. This method acts like a traffic director: it sends ordinary streams to `_paginate_default` and sends the exceptional criteria-evaluation stream to `_paginate_application_criteria`.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Ashby list endpoint page by page. It supports incremental syncing by sending a previous cursor as Ashby’s `syncToken`, which asks Ashby for records changed since that point.

**Data flow**: It starts with a request body containing the page size. If a cursor was supplied, it adds it as `syncToken`. It then repeatedly POSTs to the stream’s Ashby endpoint, reads the returned `results`, yields any records found, and checks whether Ashby says more data is available. If there is another page, it sends the returned `nextCursor` on the next request; if not, it stops.

**Call relations**: `paginate` calls this for all ordinary Ashby streams, such as candidates, jobs, offers, users, and lookup tables. It relies on the connector’s POST helper to do the actual network request, and it hands record batches back upward to the sync framework.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads criteria evaluations, which Ashby only exposes by application ID rather than as one simple list. It first finds applications, then asks for the evaluations attached to each one.

**Data flow**: It pages through `/application.list` to get applications. For each application record that has an ID, it POSTs that ID to `/application.listCriteriaEvaluations`. It copies each returned evaluation into a new row, makes sure the row includes the `applicationId`, groups the rows for that application, and yields them when present. It keeps paging through applications until Ashby reports there are no more.

**Call relations**: `paginate` calls this only for the `application_criteria_evaluations` stream. It performs a fan-out flow: one application list page can trigger many follow-up detail requests, and the resulting evaluation records are then passed back to the normal sync pipeline.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/greenhouse.py`

`io_transport` · `source sync`

Greenhouse exposes recruiting information such as candidates, jobs, applications, interviews, offers, users, and many reference lists. This file turns those API endpoints into named streams the rest of the sync system can ask for. Without it, the platform would not know which Greenhouse URLs to call, how to move through multi-page results, or how to connect child records back to their parent record.

Most Greenhouse endpoints return a simple list of records, so the connector can pass each page onward without unpacking a wrapper object. For normal streams, it looks up the stream name in a path table, adds a page size, and, when doing an incremental sync, adds a time filter such as “updated after this timestamp.” A few endpoints use different filter names, and this file records those exceptions.

Some streams are nested under a parent. For example, openings live under a specific job. For these, the connector first reads the parent list, then visits each parent’s child URL. It also stamps each child record with the parent id, like writing the folder name on every paper taken from that folder, so downstream code can tell where the child came from.

Authentication uses HTTP Basic auth when the API key is available directly. If an external auth broker is handling credentials, the connector leaves auth injection to that layer. If Greenhouse refuses access with 401 or 403, the stream is marked as skipped rather than failing the whole run.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream definition in one compact, consistent way. A stream definition tells the sync system the stream’s name, Greenhouse source object, id field, time fields, and whether it is one of the main canonical streams.

**Data flow**: It receives stream settings such as a name, optional source object, primary key, cursor field, and timestamp field names. It fills in sensible defaults where values are missing, then builds and returns a StreamSpec object that the connector later uses to know how to sync that stream.

**Call relations**: This function is used while the module is being loaded to declare all Greenhouse streams. It hands those definitions to the connector through the module’s stream list, so later sync code can treat each Greenhouse collection as a standard stream.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Greenhouse and adjusts authentication to match Greenhouse’s API rules. Greenhouse expects the API key as the username in HTTP Basic auth, with an empty password.

**Data flow**: It receives a base URL and a resolved credential. First it asks the parent RestConnector to make the normal client. If the credential contains a bearer value, this function treats that value as the Greenhouse API key, installs Basic auth on the client, and removes the normal Authorization header. It returns the prepared HTTP client.

**Call relations**: The broader connector setup calls this when it needs a network client for Greenhouse. It builds on the base RestConnector behavior, but changes the final authentication style by using httpx.BasicAuth because Greenhouse does not use the usual bearer-token header in this direct-key case.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This chooses the query parameter name Greenhouse expects for incremental syncing. Most streams use updated_after, but a few streams use a different name.

**Data flow**: It receives a stream name. It checks the exception table for that stream and returns the special parameter name if one exists; otherwise it returns the default updated_after.

**Call relations**: The paginate method calls this when it is building request parameters for a stream with a saved cursor. This keeps the main paging code simple while still honoring Greenhouse endpoint differences.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Greenhouse streams. Given a stream and an optional cursor timestamp, it yields pages of records from the right Greenhouse endpoint.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. If the stream is a nested per-parent stream, it delegates to the per-parent paging flow. Otherwise it looks up the stream’s URL path, prepares parameters such as page size and an incremental time filter, then yields each page returned through link-header pagination. If Greenhouse responds with 401 or 403, it converts that into a StreamSkipped signal so the run records that this stream could not be read instead of treating it as a crash.

**Call relations**: This method is called by the general source sync machinery whenever records are needed for a Greenhouse stream. It decides whether to hand off to _paginate_per_parent for nested data, to _paginate_link_header for simple top-level data, or to _cursor_param when it needs the correct incremental filter name. When access is refused, it creates a StreamSkipped error for the sync layer to record.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This follows Greenhouse’s normal page-by-page navigation. Greenhouse tells clients where the next page is through a Link header, which is a standard HTTP way to point to related pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST helper to fetch pages using Greenhouse’s page size and Link header, then yields each list of records as it arrives.

**Call relations**: The main paginate method uses this for ordinary top-level streams. The per-parent paging method also uses it twice: once to walk parent records and again to walk each parent’s child records.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that only exist underneath another record, such as a job’s openings or a user’s job permissions. It preserves the relationship by adding the parent id to each child record.

**Data flow**: It receives an HTTP client, the parent collection path, a child URL template, and the field name where the parent id should be written. It pages through parents, takes each parent’s id, fetches that parent’s child pages, adds the parent id to each child dictionary if it is not already present, and yields those child pages onward.

**Call relations**: The main paginate method calls this when the requested stream is one of Greenhouse’s nested streams. This method relies on _paginate_link_header for both parent and child API calls, so nested streams use the same pagination rules as top-level streams.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/recruitee.py`

`io_transport` · `request handling`

Recruitee is a recruiting tool, and this connector is the small adapter that knows how Recruitee’s API is shaped. Its job is read-only: it does not create or change anything in Recruitee. It only asks Recruitee for lists of records and passes them back to the wider source-sync system.

The file defines three streams, which are the kinds of things the system can fetch: candidates, offers, and departments. Each stream says what API object to ask for and which field is the unique identifier, usually called the primary key. Candidates and offers are marked as canonical, meaning they are treated as main, first-class synced objects; departments are still synced but are more supporting data.

Recruitee uses page-number pagination, which means the connector asks for page 1, then page 2, and so on, with up to 100 records per page. It keeps going until the shared REST helper sees there are no more full pages to read. An everyday analogy is reading a long paper report one numbered page at a time.

One important safety detail is that the base web address is left empty here because Recruitee’s address depends on the customer’s company ID. The run must provide the correct tenant-specific address, so the connector does not accidentally call the wrong place. If Recruitee refuses access with a 401 or 403 response, the connector reports that this stream should be skipped, usually because the key is invalid or lacks permission.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream, such as candidates or offers, page by page. It is used when the sync engine needs the actual records from Recruitee, and it turns Recruitee’s numbered API pages into batches of dictionaries the rest of the system can process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. The cursor is accepted because the shared connector interface includes it, but Recruitee streams here are full-refresh, so the function does not use it to fetch only changes. It builds the path for the stream, asks the shared page-number helper for pages of up to 100 records, and yields each page onward. If Recruitee answers with 401 or 403, it changes that raw web error into a clear StreamSkipped message; other errors are passed upward unchanged.

**Call relations**: During a sync, the broader REST connector machinery calls this method for each declared Recruitee stream. The method delegates the repetitive page-by-page HTTP work to the shared page-number helper, then hands each returned batch back to the caller. If access is refused, it calls StreamSkipped so the sync system can understand the problem as a permission or credential issue rather than as an ordinary data page.

*Call graph*: calls 1 internal fn (__init__).


### People operations systems
Connectors that sync employee, contractor, payroll-adjacent, time, company, worker, and team records from HR operations platforms.

### `extensions/sources/ufo_ext_sources/providers/bamboohr.py`

`io_transport` · `source sync`

BambooHR is an HR system, and this file is the bridge between BambooHR and this project’s source-sync machinery. Without it, the system would not know which BambooHR web addresses to call, how to authenticate, or how to turn BambooHR’s different response shapes into a steady stream of records.

The file first defines the BambooHR streams, which are the named kinds of data that can be synced. A stream is like a labeled folder: one for the employee directory, one for detailed employee records, one for time off, and so on. BambooHR does not use normal page-by-page pagination for these endpoints. Instead, each endpoint tends to return all matching data at once, and each response may put records in a different place. The connector hides those differences.

`BambooHRConnector` builds an HTTP client with the right headers and authentication. BambooHR needs JSON to be requested explicitly, because it may otherwise return XML. It also uses HTTP Basic authentication, with the API key as the username and the literal password `x`.

During a sync, `paginate` acts like a traffic director. It looks at the stream name and sends the request to the right helper. Some helpers make one API call. Employee detail is different: it first reads the directory, then calls BambooHR once per employee. If BambooHR rejects access with a permission or authentication error, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a stream description for one kind of BambooHR data. It gives the sync system the stream’s name, where the records come from, which field identifies a record, and which date fields can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a `StreamSpec`, which is the project’s small description object for a syncable data stream.

**Call relations**: This helper is used while the file is being loaded to build the list of BambooHR streams. It hands each finished stream description to the connector class through the module-level stream list.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to BambooHR. It sets time limits, asks BambooHR for JSON, and applies the available authentication method.

**Data flow**: It receives a base URL and a credential. It trims the base URL, prepares JSON-friendly request headers, and builds an asynchronous HTTP client. If the credential already has a special transport, it uses that unchanged. If the credential has a direct API key, it sends that key using Basic authentication with password `x`. If no usable authentication is present, it raises an error.

**Call relations**: The broader REST connector machinery calls this when a BambooHR sync needs a client. The client it returns is then passed into `paginate` and the fetch helpers so they can make actual BambooHR API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a BambooHR stream. It decides which BambooHR endpoint helper to use based on the stream name and yields batches of records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It checks the stream name, calls the matching fetch helper, and yields each batch that helper produces. If BambooHR replies with a 401 or 403 refusal, it turns that into a `StreamSkipped` message explaining that the key or permissions are not enough.

**Call relations**: The sync engine asks `paginate` for records from a selected stream. `paginate` then hands control to `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports`, depending on what is being synced.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads BambooHR’s employee directory. The directory is the broad list of employees, and it is also useful as a starting point for fetching detailed employee records elsewhere.

**Data flow**: It uses the HTTP client to request the employee directory endpoint. From the returned JSON data, it looks for the `employees` list. If that list has records, it yields the list as one batch; if it is empty, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is the employee directory. It relies on the base REST connector’s GET helper to make the actual web request, then gives the resulting employee batch back up to `paginate`.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads detailed records for each employee. BambooHR does not provide those details as one simple list here, so the function first gets the directory and then asks for each employee one by one.

**Data flow**: It requests the employee directory, reads the employee rows, and skips anything that is not a usable dictionary or has no employee ID. For each valid ID, it requests that employee’s detail endpoint. If the returned detail is a dictionary, it makes sure the detail includes the employee ID and yields that single employee as its own batch.

**Call relations**: `paginate` calls this for the detailed employees stream. It uses the directory as a map, then performs individual detail requests. Yielding one employee at a time lets the surrounding sync process make progress and checkpoint sooner instead of waiting for every employee detail call to finish.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads time off requests from BambooHR for a date range. It uses the previous sync cursor, when available, so later syncs can start from the last known date instead of always starting from scratch.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into BambooHR `start` and `end` parameters, requests the time off endpoint, and accepts either a plain list response or a response with records under `requests`. If records are found, it yields them as one batch.

**Call relations**: `paginate` calls this for the time off stream. Before making the request, it delegates date-range formatting to `_date_window_params`, because the same date-window rule is also needed by timesheet syncing.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads BambooHR timesheet entries for a date range. Like time off syncing, it uses a cursor so the request can focus on the relevant window of time.

**Data flow**: It receives the HTTP client and optional cursor. It converts that cursor into `start` and `end` request parameters, calls the timesheet entries endpoint, and looks for records either as a plain list or under `entries`. If there are records, it yields them as one batch.

**Call relations**: `paginate` calls this for the timesheet entries stream. It shares `_date_window_params` with the time off fetcher so both endpoints use the same start-and-end date logic.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads BambooHR’s field catalog, which describes available employee-related fields. This helps the sync know what fields BambooHR exposes.

**Data flow**: It requests the metadata fields endpoint. It accepts either a response that is already a list or one where the list appears under `fields`. If records are present, it yields them as a batch.

**Call relations**: `paginate` calls this when the metadata fields stream is selected. The helper normalizes BambooHR’s response shape before handing records back to the sync flow.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function asks BambooHR to build a custom employee report with a chosen set of useful fields. It is used when the connector wants a compact employee report rather than raw endpoint-specific data.

**Data flow**: It creates a request body with a report title and a list of desired fields such as name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to the custom reports endpoint. It then reads rows from the returned `employees` list and yields them if any exist.

**Call relations**: `paginate` calls this for the custom reports stream. Unlike the simple GET-based fetchers, this helper posts a report definition to BambooHR and receives the generated report rows in response.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper creates the date range BambooHR requires for time-based endpoints. It turns the sync cursor into a BambooHR-friendly start date and supplies a far-future end date.

**Data flow**: It receives an optional cursor. If the cursor is present, it trims it and uses the first 10 characters as a `YYYY-MM-DD` date. If no cursor is present, it starts at `1970-01-01`, meaning a fresh sync asks for everything. It returns a dictionary with `start` and `end` values.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this before making their API requests. It keeps the date-window rule in one place so both fetchers ask BambooHR for data in the same way.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/providers/deel.py`

`io_transport` · `active during Deel source syncs when records are fetched from the remote API`

This file is a read-only connector for Deel, an HR and payroll service. Its job is to fetch records from Deel’s REST API, which is a web interface where data is requested through URLs, and present them in the common stream format used by the larger source-sync system. Without this file, the project would not know which Deel objects exist, which ones can be synced incrementally, or how to walk through Deel’s pages of results.

The file first defines a small helper for building stream descriptions. A stream is one kind of record to sync, like “contracts” or “payslips.” Each stream says what its main ID field is, whether it has an update timestamp, and whether it is a central or “canonical” stream.

It then lists the five Deel streams this connector can read. Most use an `updated_at` field so future syncs can ask Deel only for records changed after the last saved point. Forms do not have that cursor field, so they are fully re-read each time.

The `DeelConnector` class supplies Deel’s base API address and the paging behavior. Deel returns records in chunks using `limit` and `offset`, like asking for books 1–100, then 101–200, and so on. The connector keeps requesting pages until Deel returns no records or fewer than the page size.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Deel data stream, such as contracts or tasks. It keeps the stream list short and consistent, so each stream is described in the same way.

**Data flow**: It receives a stream name plus optional details like the Deel API object name, the primary ID field, the update timestamp field, and whether the stream is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the shared object the sync system uses to know how to read that stream.

**Call relations**: This helper is used when the file builds the fixed `DEEL_STREAMS` list. It hands each finished stream description to the connector class through that list, so later sync code can ask the connector what Deel data types are available.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the starting query parameters for a Deel API request. Query parameters are the small pieces of information added to a URL, such as how many records to return or which records changed after a certain time.

**Data flow**: It receives a stream description and an optional saved cursor value, which represents the last known update point. It always asks Deel for up to 100 records. If there is a cursor and the stream supports update-based syncing, it also adds `updated_after`, telling Deel to return only newer or changed records. It returns this parameter dictionary for use in requests.

**Call relations**: `DeelConnector.paginate` calls this before it starts requesting pages. The returned parameters become the base request options, and `paginate` adds the changing `offset` value for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls usable record objects out of Deel’s API response. It protects the rest of the connector from odd or unexpected response shapes by returning only dictionary-like records.

**Data flow**: It receives raw response data from the API. If the response is a dictionary with a `data` list inside, it keeps only the items in that list that are themselves dictionaries. If the response is already a list, it does the same filtering there. If neither shape matches, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after every API request. It turns the raw response into the clean list of records that `paginate` can yield to the rest of the sync system.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function walks through all pages of one Deel stream and yields batches of records. It is the main reading loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the correct Deel API path, prepares the base query parameters, then starts at offset 0. For each loop, it requests one page from Deel, extracts valid records, and yields them as a batch. If Deel returns no records, or returns fewer than the page size, it stops. Otherwise, it moves the offset forward by 100 and asks for the next page.

**Call relations**: The broader source-sync framework calls this when it needs records for a Deel stream. Inside the loop, it relies on `_initial_params` to prepare the request and `_extract_records` to clean the response. It hands each batch of records back to the framework, which can then store or process them.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/providers/rippling.py`

`io_transport` · `source sync run`

This connector is the project’s read-only bridge to Rippling, a workforce and HR platform. Without it, the system would not know which Rippling URLs to call, how to ask for only recently changed workers or teams, or how to keep following Rippling’s “next page” links until all records are fetched.

The file defines three streams: companies, workers, and teams. A stream is a named kind of data the sync system can pull. Workers and teams support incremental syncing, meaning the connector can ask Rippling for records updated after a saved timestamp. Companies do not have that kind of cursor here, so they are fetched as a full refresh.

The main class, `RipplingConnector`, supplies the Rippling base URL and the list of supported streams. Its `paginate` method does the actual reading: it builds the first request, downloads a page, extracts records from the response, yields those records to the caller, then follows Rippling’s `next` link like turning pages in a book. Rippling may return that next link as a full web address or just a path, so the connector normalizes it before the next request.

One important behavior is permission handling. If Rippling rejects a request with 401 or 403, the connector raises `StreamSkipped`, which tells the sync runner that this stream cannot be read with the current credentials instead of treating it like a normal data page.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s “next page” link into the path the connector should request next. It accepts either a full URL or a relative path, because Rippling may return either shape.

**Data flow**: It receives a possible next-page link. If there is no link, it returns nothing, meaning pagination is finished. If the link is a full URL, it uses `urlparse` to split it apart, keeps only the path and query string, and returns that smaller request path. If the link is already relative, it returns it unchanged.

**Call relations**: `RipplingConnector.paginate` calls this after each downloaded page. The returned path becomes the next page to request; if it returns nothing, `paginate` stops reading that stream.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first request to Rippling. It sets a page size and, when possible, adds the saved cursor so the sync only asks for records changed since the last run.

**Data flow**: It receives the stream being read and an optional cursor value, usually a timestamp from a previous sync. It always starts with `limit` set to the connector’s page size. If the stream supports a cursor and a cursor was provided, it adds `updatedAfter` with that timestamp. It returns the finished parameter dictionary.

**Call relations**: `RipplingConnector.paginate` uses this before making its first API call. After the first request, `paginate` clears these parameters because later pages are driven by Rippling’s own `next` links.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls the actual record objects out of a Rippling response, even when Rippling wraps them in slightly different shapes. It filters out anything that is not a dictionary-like record.

**Data flow**: It receives the decoded response data and the stream definition. If the response is a dictionary, it first looks for a list under the stream’s name, such as `workers` or `teams`. If that is not present, it looks for a generic `data` list. If the whole response is already a list, it uses that. In every case, it returns only items that are record dictionaries; otherwise it returns an empty list.

**Call relations**: `RipplingConnector.paginate` calls this after each API response is downloaded. The resulting list is what `paginate` yields onward as a batch of usable records.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It fetches page after page from Rippling, yields batches of records, and stops when Rippling no longer provides a next-page link.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It builds the starting API path from the stream, creates the first query parameters, then repeatedly requests a page. Each response is decoded by the inherited `_get` request helper, passed through `_extract_records`, and yielded if it contains records. It then uses `_next_path` to decide where to go next. If Rippling returns 401 or 403, it changes that HTTP failure into `StreamSkipped`; other HTTP errors are allowed to rise normally.

**Call relations**: The wider sync runner calls this when it wants records for one Rippling stream. Inside the loop, it relies on `_initial_query` to shape the first request, `_extract_records` to find usable rows in each response, and `_next_path` to continue pagination. When access is refused, it hands control back to the runner by raising `StreamSkipped`, signaling that the stream should be skipped because the credential is missing permission or is invalid.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

# HR, recruiting, and workforce source connectors  `stage-14.6`

This stage is part of the system’s data-gathering layer. It runs when a sync job needs to bring in people-related records from outside services. Each connector knows how to talk to one vendor’s web API, meaning the online doorway that service provides for software to request data. The connector asks for records page by page, then presents them as steady streams the rest of the system can store, search, or reuse.

Ashby, Greenhouse, and Recruitee cover recruiting. They pull hiring records such as candidates, jobs, applications, interviews, offers, departments, and users. BambooHR covers employee administration, including directories, employee details, time off, timesheets, field definitions, and reports. Deel covers contractor and HR operations, such as contracts, forms, payslips, timesheets, and tasks. Rippling covers workforce structure, including companies, workers, and teams.

Together, these files act like adapters for different plug shapes. Each outside system is different, but this stage makes their data look consistent enough for the shared sync machinery to process.

## Files in this stage

### Recruiting sources
Connectors that sync hiring pipelines, candidates, jobs, applications, interviews, offers, and recruiting users from applicant-tracking systems.

### `extensions/sources/ufo_ext_sources/ashby.py`

`io_transport` · `during source sync runs`

Ashby is a recruiting platform, and its API does not return everything in one big download. Instead, the connector must ask for one page at a time, remember whether more pages exist, and continue until Ashby says it is done. This file defines that reading behavior.

First, it lists the Ashby data streams the system knows about, such as candidates, job postings, applications, users, and metadata like departments or tags. Each stream says which Ashby endpoint to call and which field marks changes over time, usually `updatedAt`. That change marker is important because it lets later syncs ask only for records that changed since the last run instead of re-reading everything.

The `AshbyConnector` then supplies the practical details. It builds an HTTP client with Ashby's required authentication: the API key is sent using HTTP Basic authentication, which is a standard username-and-password style header; here the API key is the username and the password is empty. For normal streams, it posts to Ashby's list endpoint with a page size and optional sync token, then follows Ashby's `nextCursor` until there is no more data.

One stream is special: application criteria evaluations are not listed globally. The connector first lists applications, then asks Ashby for criteria evaluations for each application id. Without this file, the system would know neither which Ashby objects are available nor how to page through and authenticate Ashby's read-only API.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Ashby stream, such as candidates or jobs. It keeps the stream list short and consistent by filling in common details like the creation time field and update time field.

**Data flow**: It receives a friendly stream name, the Ashby API path to call, and optional details such as the primary key and cursor field. It packages those values into a `StreamSpec`, which is the system's small instruction card for how to read that stream. The result is a stream definition used later by the connector.

**Call relations**: The file uses this helper while building `ASHBY_STREAMS`. Each call produces one entry in the connector's catalog, and those entries are later read by the base source system when deciding which Ashby endpoints to sync.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to Ashby and makes sure the API key is sent in the format Ashby expects. If a credential already provides a special transport, such as a brokered or proxied connection, it leaves that path alone.

**Data flow**: It receives a base URL and a credential. If the credential contains a custom transport, it passes everything to the parent connector's client builder. Otherwise, it expects a direct API key in `credential.bearer`, turns that key into an HTTP Basic authentication header by base64-encoding `api_key:` with an empty password, and returns an async HTTP client configured with that header. If no API key is present, it raises an error instead of making unauthenticated requests.

**Call relations**: The wider connector framework calls this when it needs a client for an Ashby sync. This method adapts the generic REST connector behavior to Ashby's particular authentication rule, then hands the actual client creation back to the parent `RestConnector`.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading pages from Ashby. It decides whether a stream can use the normal paging flow or needs the special per-application lookup flow.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from a previous sync. If the requested stream is `application_criteria_evaluations`, it yields pages produced by the special application fan-out reader. For every other stream, it yields pages from the normal Ashby list reader, passing along the cursor so Ashby can return only changed records when possible.

**Call relations**: The base sync machinery calls this when it wants records for a stream. This method then hands the work to either `_paginate_default` or `_paginate_application_criteria`, depending on the stream name, and passes each produced page back up to the rest of the sync pipeline.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ordinary Ashby list endpoints one page at a time. It is used for streams where Ashby exposes a direct `something.list` endpoint, such as candidates, jobs, or users.

**Data flow**: It starts with a request body containing the page size. If a previous sync cursor exists, it adds that as Ashby's `syncToken`, meaning Ashby should limit the response to records changed since that token. It posts to the stream's endpoint, yields the `results` list when records are present, then checks whether Ashby reports more data. If there is another page, it sends the returned `nextCursor` on the next request; if not, it stops.

**Call relations**: `paginate` calls this for all normal streams. It performs the repeated POST requests and returns record batches upward, while relying on the connector's inherited `_post` method to do the actual network call.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads application criteria evaluations, which require an extra step because Ashby stores them under individual applications rather than offering one global list. It is like first reading a list of folders, then opening each folder to collect the papers inside.

**Data flow**: It pages through `/application.list` to get application records. For each application dictionary with an id, it posts to `/application.listCriteriaEvaluations` with that application id. It copies each returned evaluation, makes sure the `applicationId` is included, gathers the evaluations for that application, and yields them as a page when there is anything to return. It continues through all application pages until Ashby says there is no more application data or fails to provide a next cursor.

**Call relations**: `paginate` calls this only for the `application_criteria_evaluations` stream. This function uses the inherited `_post` network helper twice in the flow: first to list applications, then to fetch evaluations for each application id, before handing completed batches back to the sync pipeline.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/greenhouse.py`

`io_transport` · `source sync`

Greenhouse Harvest is a web API for recruiting data. This file is the read-only connector for it: it knows which Greenhouse URLs to call, how to authenticate, how to follow Greenhouse pagination, and how to skip streams the API key is not allowed to read. Without this file, the project would not know how to pull Greenhouse recruiting records into its normal source-sync pipeline.

The file first defines a catalog of streams. A stream is one kind of data to sync, like candidates or jobs. Some streams are simple top-level lists. Others are child lists under a parent record, such as interviews under an application or openings under a job. For those child streams, the connector first reads the parent list, then asks Greenhouse for each parent’s children, and adds the parent id onto each child record. That is like labeling every item from a box with the box it came from before mixing the items together.

Greenhouse returns pages using HTTP Link headers, so the connector keeps following the “next page” link until there is no next page. For incremental streams, it sends a date cursor so Greenhouse can return only newer records. If Greenhouse replies with “unauthorized” or “forbidden,” the stream is marked as skipped instead of failing the whole run.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream description in one short call. It keeps the long list of Greenhouse streams readable by filling in common defaults, such as using `id` as the main record key.

**Data flow**: It takes a stream name plus optional details like the Greenhouse object name, cursor field, and timestamp fields. It fills in defaults where values were not provided, then returns a `StreamSpec`, which is the project’s standard description of one syncable collection.

**Call relations**: The module uses this helper while it is being loaded to build the Greenhouse stream catalog. It hands the finished stream descriptions to `StreamSpec.__init__`, and those stream descriptions later guide `GreenhouseConnector.paginate` when the sync runner asks for records.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Greenhouse and adjusts authentication to match Greenhouse’s rules. Greenhouse expects HTTP Basic authentication, where the API key is the username and the password is blank.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent connector class to create the normal HTTP client. If the credential contains a bearer-style key on this host, it replaces the usual bearer-token header with Basic authentication using that key, then returns the prepared client.

**Call relations**: This is the connector’s client-building hook, used when the source framework prepares to call Greenhouse. It relies on the base connector for the common client setup, then uses `httpx.BasicAuth` only when this connector itself must send the API key; if an auth broker is injecting credentials elsewhere, it leaves the base client behavior in place.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This chooses the query-string parameter Greenhouse expects for incremental syncing. Most streams use `updated_after`, but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It looks for that name in the small exception table and returns the special parameter if present; otherwise it returns the default parameter name, `updated_after`.

**Call relations**: `GreenhouseConnector.paginate` calls this when it has a saved cursor and needs to ask Greenhouse for only records after that point. This keeps the pagination code simple while preserving the endpoint-specific naming differences.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main record-fetching path for a Greenhouse stream. Given one stream and an optional saved cursor, it yields pages of records from the correct Greenhouse endpoint.

**Data flow**: It receives an HTTP client, a stream description, and possibly a cursor value from a previous sync. If the stream is a child stream, it delegates to the per-parent pagination path. Otherwise it finds the stream’s normal URL, adds `per_page=500`, adds the right cursor parameter when needed, and yields each page returned by Greenhouse. If Greenhouse says access is denied, it turns that into a clean stream skip; other HTTP errors still bubble up as failures.

**Call relations**: The source sync runner calls this when it wants data for a specific Greenhouse stream. This function decides which lower-level paging helper to use: `_paginate_per_parent` for nested child data, `_paginate_link_header` for normal endpoint data, and `_cursor_param` to name incremental filters correctly. When access is refused, it raises `StreamSkipped` so the wider run can record a skip instead of treating the whole sync as broken.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This follows Greenhouse’s page-by-page navigation for one endpoint. It hides the details of Link-header pagination, where the server tells the client the URL of the next page.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST connector to fetch pages using the configured page size, then yields each list of records exactly as it arrives.

**Call relations**: `GreenhouseConnector.paginate` uses this for ordinary top-level streams, and `GreenhouseConnector._paginate_per_parent` uses it both for parent lists and child lists. It is the shared paging tool that keeps all Greenhouse endpoint reads moving through the same page-following behavior.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads nested Greenhouse data that only exists under another record, such as a job’s openings or a candidate’s activity feed. It preserves the relationship by adding the parent id onto each child record.

**Data flow**: It receives the parent endpoint path, a child endpoint template, and the field name where the parent id should be stored. It pages through the parent records, takes each parent’s `id`, fetches that parent’s child pages, adds the parent id to each child dictionary if it is not already present, and yields the child pages onward.

**Call relations**: `GreenhouseConnector.paginate` calls this whenever the requested stream is one of the per-parent streams. This function repeatedly calls `_paginate_link_header`: first to walk the parent collection, then to walk each parent’s child collection. The stamped child records then flow back to the caller as normal stream pages.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/recruitee.py`

`io_transport` · `during source sync`

Recruitee is a recruiting tool, and its API gives data back in numbered pages, much like search results with “page 1”, “page 2”, and so on. This file defines a Recruitee connector that knows which Recruitee lists matter to this project: candidates, offers, and departments. Without it, the system would not know what Recruitee data to ask for, where each list lives in the API, or how to keep asking for the next page until all records are read.

The connector is read-only. It does not create or update anything in Recruitee. Its job is only to fetch records. Each stream has a name, the API object to request, and a primary key, which is the field used to identify one record from another.

One important safety choice is that the connector has no default base URL. Recruitee URLs depend on the customer’s company ID, so the full tenant-specific address must be supplied by the sync runner. This prevents accidentally calling the wrong Recruitee account.

When Recruitee refuses access with HTTP 401 or 403, meaning “not logged in” or “not allowed,” the connector skips that stream with a clear message instead of crashing unclearly. Other errors are allowed to surface normally.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Recruitee stream, such as candidates or offers, page by page. It is used when the sync system wants all records from that stream and needs them delivered in batches.

**Data flow**: It receives an HTTP client, a stream description, and an unused cursor value. It builds the API path from the stream, asks Recruitee for pages of up to 100 records, and yields each page as a list of record dictionaries. If Recruitee replies with 401 or 403, it turns that into a clear “stream skipped” result; if any other HTTP error happens, it lets that error continue upward.

**Call relations**: During a Recruitee sync, the wider source runner calls this method for each configured stream. The method relies on the shared REST connector paging behavior to do the repeated page-number requests. If Recruitee refuses access, it creates a StreamSkipped error so the sync can report that this particular stream could not be read because of missing permission or an invalid key.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce sources
Connectors that sync employee, contractor, payroll-adjacent, time, company, worker, and team records from HR and workforce platforms.

### `extensions/sources/ufo_ext_sources/bamboohr.py`

`io_transport` · `source sync run`

BambooHR is a human resources service, and its API does not behave like many standard web APIs. Some endpoints return all data at once instead of using normal page numbers, and different endpoints wrap their records in different shapes. This file hides those differences behind one connector, so the rest of the system can ask for a stream of records without knowing BambooHR’s quirks.

The file first defines the BambooHR streams the system can read. A stream is one category of data, like “employees_directory” or “time_off_requests.” The BambooHRConnector then builds an HTTP client with the right base address, headers, timeouts, and authentication. BambooHR requires HTTP Basic authentication, using the API key as the username and the literal password “x.” It also requires an “Accept: application/json” header, because otherwise it may return XML instead of JSON.

The central method is paginate. Even though BambooHR does not really paginate in the usual sense, the rest of the sync system expects batches of records. So paginate acts like a traffic director: it looks at the requested stream name and sends the work to the matching fetch method. Some fetch methods make one request and yield one batch. Employee detail is different: it first reads the directory, then asks BambooHR for each employee one by one. If BambooHR rejects access with a 401 or 403 response, the stream is skipped with a clear message rather than failing mysteriously.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a stream description for one BambooHR data category. It keeps the stream setup short and consistent, so each stream can declare its name, key field, date fields, and whether it is a main canonical stream.

**Data flow**: It receives details such as a stream name, optional source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, such as using the stream name as the source object when none is provided. It returns a StreamSpec object that the connector uses later to know what data can be synced.

**Call relations**: This function is used while the file is being loaded to build the BAMBOOHR_STREAMS list. It hands each stream’s plain settings into StreamSpec, which becomes the shared description used later by BambooHRConnector.paginate.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the web client used to talk to BambooHR. It makes sure requests go to the right tenant URL, use JSON, have safe time limits, and carry the right authentication.

**Data flow**: It receives a base URL and a resolved credential. It trims any trailing slash from the URL, prepares JSON headers, and creates an HTTP timeout. If the credential includes a custom transport, it uses that transport unchanged. Otherwise, if the credential contains an API key, it builds Basic authentication with that key and the password “x.” It returns an httpx AsyncClient ready to make BambooHR requests, or raises an error if no usable authentication is present.

**Call relations**: The broader source framework calls this when it is preparing to run the BambooHR connector. This method hands back the configured HTTP client that all later fetch methods use through the connector’s GET and POST helpers.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading BambooHR streams. It turns a requested stream name into the correct BambooHR API calls and yields records in batches that the sync system can consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the last remembered position from a previous sync. It checks the stream name and delegates to the matching fetch method. Each fetch method yields one or more lists of records, and paginate passes those lists onward. If BambooHR responds with 401 or 403, it converts that refusal into a StreamSkipped error with a human-readable reason.

**Call relations**: The sync engine calls paginate when it wants records for a particular BambooHR stream. paginate then calls _fetch_directory, _fetch_employees, _fetch_time_off, _fetch_timesheets, _fetch_meta_fields, or _fetch_custom_reports depending on the stream. It is the connector’s traffic director, keeping the rest of the system from needing to know which BambooHR endpoint belongs to which stream.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR’s employee directory. The directory is the broad employee list and is also used as the starting point for fetching detailed employee records.

**Data flow**: It receives the HTTP client, sends a GET request to the employee directory endpoint, and looks for records under the “employees” field in the response. If any employee records are present, it yields them as one batch. If the response contains no employees, it yields nothing.

**Call relations**: BambooHRConnector.paginate calls this when the requested stream is employees_directory. It relies on the connector’s lower-level GET helper to make the actual web request, then passes the resulting employee list back to paginate.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches detailed employee records one employee at a time. It exists because BambooHR’s detailed employee endpoint requires an employee ID, so the connector must first discover those IDs from the directory.

**Data flow**: It receives the HTTP client and first requests the employee directory. It walks through the returned directory rows, ignores malformed rows or rows without an ID, and then requests the detailed endpoint for each valid employee ID. For each employee detail response, it makes sure the ID is present and yields that single employee record as its own batch.

**Call relations**: BambooHRConnector.paginate calls this when the requested stream is employees. This method uses the directory as a lookup list, then fans out into one request per employee. Yielding one employee at a time lets the sync system make progress checkpoints quickly instead of waiting for every employee detail request to finish.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR time-off requests for a date range. It uses the sync cursor to avoid always starting from scratch when a previous run already read older data.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into BambooHR’s required start and end date parameters, sends a GET request to the time-off requests endpoint, and accepts either a plain list response or a response with records under “requests.” If records exist, it yields them as one batch.

**Call relations**: BambooHRConnector.paginate calls this for the time_off_requests stream. Before making the request, it calls _date_window_params to build the date window BambooHR requires. The resulting records flow back through paginate to the sync engine.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR timesheet entries for a date range. Like the time-off reader, it uses the cursor to choose the starting date for the request.

**Data flow**: It receives the HTTP client and an optional cursor. It converts the cursor into start and end date parameters, requests the timesheet entries endpoint, and accepts either a plain list response or a response with records under “entries.” If records are found, it yields them as one batch.

**Call relations**: BambooHRConnector.paginate calls this for the timesheet_entries stream. It calls _date_window_params to prepare the required date range, then hands the fetched timesheet records back to paginate for the wider sync flow.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads BambooHR’s field catalog. The field catalog describes what employee-related fields BambooHR knows about, which can help the system understand available data.

**Data flow**: It receives the HTTP client, sends a GET request to the metadata fields endpoint, and accepts either a plain list response or a response with records under “fields.” If field records are present, it yields them as one batch.

**Call relations**: BambooHRConnector.paginate calls this when the requested stream is meta_fields. It makes the specific BambooHR metadata request and returns the normalized list of fields to the common pagination flow.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method asks BambooHR to create and return a custom report with a fixed set of useful employee fields. It is used when the connector wants a curated employee table rather than every possible field.

**Data flow**: It receives the HTTP client, builds a JSON request body naming the report and listing fields such as employee ID, display name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to the custom report endpoint. It then reads returned rows from the “employees” field and yields them as one batch if any exist.

**Call relations**: BambooHRConnector.paginate calls this for the custom_reports stream. This is the only fetch path in the file that uses POST rather than GET, because BambooHR requires a submitted report definition before it returns the report rows.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the date range parameters required by BambooHR’s time-off and timesheet endpoints. It gives fresh syncs a very early start date, and resumed syncs a start date based on the saved cursor.

**Data flow**: It receives an optional cursor. If there is no cursor, it uses “1970-01-01” as the start date so the request can include historical data. If a cursor is present, it trims whitespace and takes the first 10 characters, matching the “YYYY-MM-DD” date format BambooHR expects. It always returns a dictionary with that start date and a far-future end date of “2100-01-01.”

**Call relations**: The time-off and timesheet fetch methods call this just before making their API requests. It keeps their date-window behavior identical, so both streams interpret the sync cursor in the same simple way.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/deel.py`

`io_transport` · `data sync`

This connector is a read-only bridge between the system and Deel, an HR and payroll service. Without it, the system would not know which Deel objects exist, where to fetch them, how to authenticate through the shared REST connector, or how to move through Deel's paginated API responses.

The file first defines a small helper for describing a Deel stream. A stream is one kind of object to sync, like contracts or payslips. Each stream says what its name is, which field uniquely identifies a record, and whether it can be synced incrementally using an update timestamp. Most Deel streams use an `updated_at` field so the connector can ask only for records changed since the last run. Forms do not have that cursor, so they are fetched fully each time.

The `DeelConnector` class then provides the details needed to talk to Deel: the connector name, the base API address, the list of streams, and the pagination behavior. Deel returns records in batches using `limit` and `offset`, like reading a long report one page at a time. The connector starts at offset zero, asks for up to 100 records, extracts the actual records from Deel's response, yields them to the rest of the sync system, and keeps going until Deel returns fewer than 100 records or no records at all.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard stream description for one type of Deel object. It keeps the stream definitions short and consistent, so each Deel object can be listed with only the settings that differ from the default.

**Data flow**: It receives a stream name and optional details such as the Deel API object name, the unique ID field, the update timestamp field, and whether the stream is canonical. It fills in sensible defaults, then produces a `StreamSpec`, which is the shared description the connector framework uses to know what to fetch.

**Call relations**: This is used while the file is loaded to build the `DEEL_STREAMS` list. It hands those stream descriptions to `StreamSpec`, and the resulting list is later used by `DeelConnector` when the sync system asks which Deel data can be read.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the starting query parameters for a Deel API request. It always asks for one page of up to 100 records, and when possible it adds a timestamp filter so only recently changed records are requested.

**Data flow**: It receives a stream description and an optional saved cursor, which is usually the last synced update time. It creates a parameter dictionary with `limit` set to the page size; if the stream supports incremental syncing and a cursor is present, it also adds `updated_after`. The result is a dictionary ready to be sent with an HTTP request.

**Call relations**: `DeelConnector.paginate` calls this before it starts walking through pages for a stream. The parameters it returns become the base request settings, and `paginate` adds the changing `offset` value for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls usable record objects out of a Deel API response. It protects the rest of the connector from unexpected response shapes by returning only dictionary-like records and ignoring anything else.

**Data flow**: It receives raw response data from the API. If the response is a dictionary with a `data` list, it keeps only the items in that list that are dictionaries. If the response itself is a list, it does the same filtering directly. If the response is neither shape, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each HTTP request. The extracted records are what `paginate` yields back to the wider sync system; an empty result tells `paginate` that there is nothing more useful to send onward.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for Deel streams. It repeatedly requests pages from Deel's REST API and yields each batch of records to the sync system until the stream is exhausted.

**Data flow**: It receives an asynchronous HTTP client, a stream description, and an optional cursor from a previous sync. It builds the API path for that stream, prepares the base parameters, then repeatedly adds an `offset`, performs a GET request, extracts records, and yields each non-empty batch. If no records come back, or if a batch is smaller than the page size, it stops because there are no more pages to read.

**Call relations**: The broader REST connector framework calls this when it needs records for a Deel stream. Inside the loop it relies on `_initial_params` to prepare request filters and `_extract_records` to turn Deel's raw response into clean record batches. It also uses the inherited `_get` method from the base REST connector to actually make each HTTP request.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/rippling.py`

`io_transport` · `during source sync, while reading Rippling streams`

Rippling is an external service, so the system needs a small translator that knows Rippling’s URLs, response shapes, and error behavior. This file is that translator. It defines three readable streams: companies, workers, and teams. Workers and teams can be read incrementally, meaning the connector can ask for only items updated after a saved time. Companies do not have that kind of cursor here, so they are read as a full refresh.

The main work happens in `RipplingConnector.paginate`. It starts at the API path for the chosen stream, adds a page size, and optionally adds an `updatedAfter` filter. It then asks Rippling for one page at a time. Each response may store records under a resource-specific key, such as `workers`, or under a generic `data` key, so the connector carefully extracts only dictionary-like records. If Rippling provides a `next` link, the connector follows it until there is no next page left.

A small but important safety behavior is how permission failures are treated. If Rippling returns 401 or 403, the connector raises `StreamSkipped`, which tells the larger sync runner that this stream cannot be read because the token is invalid or lacks permission. It does not write anything back to Rippling; this is a read-only source connector.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s `next` page link into the path the HTTP client should request next. Rippling may send either a full URL or a relative path, and this normalizes both forms.

**Data flow**: It receives a possible next-page link. If the link is missing, it returns nothing. If the link is a full web address, it keeps only the path and query string, such as `/workers?cursor=...`, because the connector already knows Rippling’s base URL. If the link is already a relative path, it returns it unchanged.

**Call relations**: `paginate` calls this after each successful page fetch. It decides whether the next loop should fetch another page, and exactly which path should be fetched.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the first set of query parameters for a stream request. It sets the page size and, when possible, asks Rippling for only records updated after the saved cursor.

**Data flow**: It receives the stream description and an optional cursor value, usually a timestamp from a previous sync. It always includes `limit` set to the connector’s page size. If the stream supports a cursor and a cursor value was provided, it also adds `updatedAfter`. The result is a dictionary of query parameters for the first API request.

**Call relations**: `paginate` calls this before its first request. After the first request, `paginate` clears these parameters because later pages are reached through Rippling’s own `next` link.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls the actual rows out of a Rippling response. It hides the fact that different Rippling endpoints may wrap records in slightly different ways.

**Data flow**: It receives decoded response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream name, such as `workers` or `teams`. If that is not present, it looks for a list under `data`. If the whole response is already a list, it uses that. In all cases it keeps only items that are dictionaries, and returns a clean list of records. If nothing matches, it returns an empty list.

**Call relations**: `paginate` calls this for every page returned by Rippling. The extracted records are the batches that `paginate` yields to the rest of the sync system.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Rippling streams. It walks through all available pages for one stream and yields batches of records as they are found.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the starting API path and first query parameters, fetches a page, extracts records, yields any non-empty batch, then follows the response’s `next` link. This repeats until there is no next page. If Rippling refuses access with 401 or 403, it turns that into a `StreamSkipped` error with a clear explanation; other HTTP errors are passed upward unchanged.

**Call relations**: The larger source-sync runner calls this when it wants records for companies, workers, or teams. Inside the loop, this function relies on `_initial_query` to prepare the first request, `_extract_records` to clean each response into records, and `_next_path` to continue through pagination.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

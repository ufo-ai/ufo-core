# HR and recruiting connectors  `stage-14.1.9`

This stage is part of the system’s regular data-collection work. It connects to outside workforce and recruiting tools and brings their records into the sync pipeline. A connector is like an adapter plug: each service has its own API, meaning a web doorway for requesting data, and these files translate those doorways into a common stream of records.

The Ashby, Greenhouse, and Recruitee connectors focus on recruiting. They fetch candidates, jobs, applications, interviews, offers, departments, and similar hiring data. Greenhouse also knows how to fetch nested details, such as interviews belonging to a specific application.

The BambooHR, Deel, and Rippling connectors focus more on employee and workforce operations. BambooHR reads HR datasets from its REST API. Deel brings in contracts, payslips, timesheets, tasks, and forms. Rippling reads company, worker, and team data.

Together, these files handle paging, which means collecting results a batch at a time, so the rest of the system can store and process the data consistently.

## Files in this stage

### Recruiting platforms
Connectors for applicant tracking and hiring systems that sync candidates, jobs, applications, interviews, offers, departments, and related recruiting data.

### `extensions/sources/ufo_ext_sources/providers/ashby.py`

`io_transport` · `source sync`

Ashby exposes recruiting data through a web API, but it does not send everything at once. Each list request returns one page of results and, if more data exists, a cursor that works like a bookmark for the next page. This file is the connector that knows those Ashby rules.

It first defines the Ashby streams the system can read. A stream is one kind of object, such as candidates or jobs, plus details like its API path, its unique ID field, and which timestamp can be used to read only changed records later. Most streams follow the same pattern: send a POST request to an endpoint like `/candidate.list`, include a page size, optionally include a sync token, and keep asking for the next page until Ashby says there is no more data.

Authentication has one Ashby-specific twist. Ashby uses HTTP Basic authentication, where the API key is placed in the username slot and the password is empty. `_make_client` converts the stored key into that format unless a proxy transport is already being used.

One stream is special: `application_criteria_evaluations`. Ashby does not list those directly across all applications. The connector first lists applications, then asks for criteria evaluations for each application ID, like checking each folder inside a filing cabinet. If Ashby rejects a stream because the API key lacks permission, the connector skips that stream cleanly instead of failing the whole sync.

#### Function details

##### `_stream`  (lines 29–47)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one Ashby data stream. This keeps the stream catalog compact and consistent instead of repeating the same fields for every Ashby object type.

**Data flow**: It receives a friendly stream name, the Ashby API path to call, and optional details such as the primary key and cursor timestamp field. It packages those choices into a `StreamSpec`, which is the system’s recipe for reading that kind of record.

**Call relations**: This helper is used while building the file’s Ashby stream list. It hands the finished stream recipe to `StreamSpec.__init__`, so the broader source framework can later know what endpoint to call and how to identify records.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 83–91)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Ashby, with Ashby’s required authentication format. Someone syncing Ashby data relies on this so requests include the right authorization header.

**Data flow**: It receives the API base URL and a credential. If the credential already includes a special transport, it lets the parent connector build the client unchanged. Otherwise, it reads the API key from the credential, encodes it as an HTTP Basic username with an empty password, and returns a client configured with the resulting Authorization header. If no API key is present, it raises an error instead of making unauthenticated requests.

**Call relations**: This method fits into connector setup, before any stream pages are requested. It uses `base64.b64encode` to create the Basic authentication token and creates a new `Credential` containing the proper header before handing client creation back to the shared REST connector behavior.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 93–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses how to read pages for a particular Ashby stream and turns API pages into batches of records. It also turns permission failures into a controlled stream skip when the API key cannot access that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For the special application criteria stream, it delegates to the per-application fanout reader. For normal streams, it delegates to the default Ashby page reader. It yields each non-empty batch of records. If Ashby returns a 401 or 403 refusal, it raises `StreamSkipped` with a clear message; other HTTP errors continue upward.

**Call relations**: This is the main paging doorway for the connector. The source framework asks it for records from a stream; it then calls either `AshbyConnector._paginate_application_criteria` or `AshbyConnector._paginate_default`. When access is refused, it creates a `StreamSkipped` error so the sync can move past a stream the API key is not allowed to read.

*Call graph*: calls 3 internal fn (__init__, _paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 111–132)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads ordinary Ashby list endpoints one page at a time. It is used for streams where Ashby can directly list records, such as candidates, jobs, users, and offers.

**Data flow**: It starts with a request body containing the page size. If a previous sync cursor exists, it sends that as Ashby’s `syncToken` so Ashby can return changed records rather than a full backfill. It posts to the stream’s endpoint, yields any returned records, then follows Ashby’s `nextCursor` bookmark while `moreDataAvailable` is true. It stops when Ashby says there is no more data or when no next cursor is provided.

**Call relations**: This function is called by `AshbyConnector.paginate` for all standard streams. It performs the repeated page-by-page requests and hands each batch of records back up to `paginate`, which exposes them to the rest of the sync system.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 134–170)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads application criteria evaluations, which require a two-step process because Ashby stores them under individual applications. It exists because this stream cannot be fetched with the normal one-endpoint list pattern.

**Data flow**: It first pages through `/application.list` to collect applications. For each valid application object with an ID, it posts that ID to `/application.listCriteriaEvaluations`. It copies each returned evaluation, adds the `applicationId` when it is missing, groups the evaluations for that application, and yields the group if there is anything to send. It continues through application pages until Ashby reports no more applications or stops providing a next cursor.

**Call relations**: This function is called by `AshbyConnector.paginate` only for the `application_criteria_evaluations` stream. It acts like a small nested walk: list applications first, then fetch details for each one, then pass the stamped evaluation rows back to the main pagination flow.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/greenhouse.py`

`io_transport` · `source sync`

Greenhouse is a recruiting system, and its Harvest API exposes data such as candidates, jobs, applications, offers, users, scorecards, departments, and more. This file is the read-only connector for that API. Without it, the larger sync system would not know which Greenhouse endpoints exist, how to authenticate, how to move through pages of results, or how to fetch child records that live under a parent record.

The file first defines many stream descriptions. A stream is one kind of data the sync can collect, like "candidates" or "jobs_stages". Each stream records useful facts such as its name, its Greenhouse path, its main identifier field, and whether it can be synced incrementally using a time field. Incremental syncing means asking Greenhouse only for records changed after the last saved point, instead of re-reading everything.

The main class, GreenhouseConnector, extends the shared REST connector. It sets the Greenhouse base address, creates an HTTP client with Greenhouse's Basic authentication style when the API key is available locally, and decides how to fetch each stream. Most streams are simple top-level API paths. Some are per-parent streams: the connector first lists parents, such as jobs or applications, then fetches each parent's children and stamps every child with the parent's id. This is like labeling every folder's papers with the folder name before putting them into one big box.

Greenhouse uses HTTP Link headers to point to the next page, so this connector follows those links until there are no more. If Greenhouse says access is forbidden or unauthorized for a stream, the connector marks that stream as skipped rather than failing the whole run.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a StreamSpec, which is the small description object the sync system uses to understand one Greenhouse data stream. It keeps the long stream list readable by filling in common defaults like the primary key being "id".

**Data flow**: It takes a stream name and optional details such as the Greenhouse source object, primary key, cursor field, and timestamp fields. It combines those details with defaults, then returns a StreamSpec object that the connector can later use to decide what to read and how to track progress.

**Call relations**: This function is used while the file is being loaded to build all the named Greenhouse streams. It hands those stream descriptions to the connector through the ALL_STREAMS list, so later sync code can ask GreenhouseConnector to read each stream.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Greenhouse and adjusts authentication to match Greenhouse's rules. Greenhouse expects Basic authentication, where the API key is used as the username and the password is blank.

**Data flow**: It receives a base URL and a resolved credential. First it asks the shared REST connector to build a normal client. If the credential contains a local API key, it replaces the usual bearer-token style header with Basic authentication using that key and removes the old Authorization header. It returns the ready-to-use HTTP client.

**Call relations**: The wider source runner calls this when setting up the connector's network client. It relies on the base RestConnector for the standard client setup, then uses httpx.BasicAuth to make the client fit Greenhouse's authentication format.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This returns the Greenhouse query parameter name used for incremental syncing of a stream. Most streams use "updated_after", but a few Greenhouse endpoints use different wording.

**Data flow**: It receives a stream name. It checks the connector's small exception table for that name and returns the special parameter if one exists; otherwise it returns the default "updated_after".

**Call relations**: GreenhouseConnector.paginate calls this when it has a saved cursor and needs to ask Greenhouse only for newer records. This keeps the pagination code simple while still honoring Greenhouse's endpoint-specific naming quirks.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading routine for a stream. It decides whether the stream is a simple Greenhouse endpoint or a nested per-parent endpoint, then yields pages of records for the rest of the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. If the stream is nested under parent records, it delegates to the per-parent pagination helper. If it is a normal top-level stream, it finds the API path, adds the page size, and adds an incremental time filter when a cursor is available. It then yields each page of JSON records. If Greenhouse responds with unauthorized or forbidden, it turns that into a StreamSkipped result instead of a hard failure.

**Call relations**: The source sync machinery calls this for each Greenhouse stream. It uses _cursor_param to choose the right incremental query parameter, _paginate_link_header for ordinary endpoints, and _paginate_per_parent for child collections. When access is refused, it raises StreamSkipped so the run can record a skipped stream and continue.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper follows Greenhouse's page-by-page navigation for endpoints that use HTTP Link headers. A Link header is a response header that says where the next page can be found.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector to fetch pages using the configured Greenhouse page size, then yields each list of records as it arrives until there is no next-page link.

**Call relations**: GreenhouseConnector.paginate uses this for simple streams, and _paginate_per_parent uses it both to list parent records and to fetch each parent's children. It acts as the common page-walking tool for all Greenhouse endpoints in this file.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams where Greenhouse stores child records under each parent record, such as openings under each job or interviews under each application. It preserves the relationship by adding the parent id onto each child record.

**Data flow**: It receives the parent API path, a child path template, and the field name where the parent id should be written. It pages through the parent records, takes each parent's id, fetches that parent's child pages, and adds the parent id to every child dictionary if it is not already present. It yields the child pages after they have been labeled.

**Call relations**: GreenhouseConnector.paginate calls this when a stream is listed in the per-parent stream map. This helper repeatedly calls _paginate_link_header: first for parent pages, then for each parent's child pages. The labeled child records then flow back to paginate and onward into the normal sync pipeline.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/recruitee.py`

`io_transport` · `source sync`

Recruitee is an online recruiting tool, and this connector is the small adapter that lets UFO fetch data from it in a predictable way. Without this file, the system would not know which Recruitee lists exist, what their record IDs are, or how to walk through Recruitee’s paged API responses.

The file defines three streams: candidates, offers, and departments. A stream is simply one kind of list the sync can read. Candidates and offers are marked as canonical, meaning they are treated as central records for this source, while departments are also fetched but are not the main focus.

The connector does not hard-code a Recruitee company address or an API token. Recruitee URLs depend on the customer’s company ID, so the base URL is left empty until the runner supplies the correct tenant-specific URL. This avoids accidentally calling the wrong account. Authentication is also supplied elsewhere through the runner’s auth proxy.

The main work is pagination. Recruitee returns records in numbered pages, like asking for page 1, then page 2, and so on, with up to 100 records per page. The connector keeps requesting pages until the shared REST helper decides there are no more. If Recruitee replies with “unauthorized” or “forbidden,” the connector skips that stream with a clear message instead of crashing the whole sync for an unclear reason.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Recruitee stream in page-sized chunks. It asks the Recruitee API for numbered pages and yields each page of records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. The cursor is not used because these Recruitee streams are read as full refreshes rather than changes since a previous point. It builds the API path from the stream name, requests pages of up to 100 records, and outputs each page as a list of record dictionaries. If Recruitee rejects the request with a 401 or 403 status, it turns that into a StreamSkipped error with a human-readable explanation.

**Call relations**: During a sync, the source framework calls this method when it needs records for candidates, offers, or departments. The method delegates the repeated page-number fetching to the shared REST connector helper. If Recruitee refuses access, it creates a StreamSkipped exception so the wider sync flow can treat that stream as unavailable because of missing permission or bad credentials, rather than as an ordinary data page.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce systems
Connectors for HRIS, payroll, contractor, worker, company, and team data from workforce management systems.

### `extensions/sources/ufo_ext_sources/providers/bamboohr.py`

`io_transport` · `source sync`

BambooHR exposes employee and HR data through web endpoints, but those endpoints do not all behave the same way. Some return a list inside an "employees" field, some return a raw list, some need a date range, and detailed employee records require first reading the directory and then asking for each employee one by one. This file hides those differences behind one connector so the rest of the system can ask for a stream of records without knowing BambooHR’s quirks.

The connector is read-only. It can pull the employee directory, detailed employee records, time-off requests, timesheet entries, BambooHR field definitions, and a custom employee report. It also sets up the HTTP client with BambooHR’s required JSON headers and Basic authentication, where the API key is used as the username and the password is the literal value "x". If a credential already supplies a special transport, such as one used by an auth broker or proxy, the connector uses that instead.

A central `paginate` method acts like a switchboard. Based on the requested stream name, it sends the work to the right fetch method. BambooHR does not use normal page-by-page pagination here, so each fetch method yields one batch when data exists, except employee detail, which yields one employee at a time so progress can be saved along the way. If BambooHR rejects access with 401 or 403, the stream is skipped with a clear message rather than failing mysteriously.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a `StreamSpec`, which is the system’s description of one BambooHR dataset that can be synced. It keeps the stream definitions short and consistent.

**Data flow**: It receives a stream name plus optional details such as the BambooHR object name, primary key, cursor field, and whether the stream is considered canonical. It fills in sensible defaults, then returns a `StreamSpec` object that the connector later advertises as available to sync.

**Call relations**: This helper is used while the file is being loaded to build the `BAMBOOHR_STREAMS` list. It hands the finished stream descriptions to `StreamSpec`, which stores the metadata the connector and sync runtime rely on later.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to BambooHR. It sets timeouts, asks BambooHR for JSON instead of its XML default, and attaches authentication.

**Data flow**: It takes a base URL and a resolved credential. It trims any trailing slash from the URL, prepares JSON headers and timeout limits, then either uses a provided transport from the credential or creates Basic authentication from the API key. The result is an `httpx.AsyncClient`, which is the reusable web client used for later requests. If no usable authentication is present, it raises an error.

**Call relations**: The wider REST connector flow calls this when it is ready to start a BambooHR sync. This function delegates the low-level web setup to `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient`, then returns the ready client to the rest of the connector.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a BambooHR stream. Even though the method is called `paginate`, BambooHR mostly returns whole datasets at once, so this method chooses the right fetching routine for each stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the last saved position from an earlier sync. It checks the stream name and forwards the request to the matching fetch method. As that method produces batches of records, `paginate` yields them onward. If BambooHR refuses access with a 401 or 403 response, it turns that into a `StreamSkipped` message explaining that the key or permissions are not sufficient.

**Call relations**: The sync runtime calls `paginate` when it wants records for a specific BambooHR stream. `paginate` then calls one of the private fetch methods, such as `_fetch_directory`, `_fetch_time_off`, or `_fetch_custom_reports`, and passes their output back to the runtime. It is the connector’s switchboard.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR’s employee directory, which is the broad list of employees. It is used when the sync wants the directory stream exactly as BambooHR provides it.

**Data flow**: It sends a GET request to the employee directory endpoint through the inherited `_get` helper. It looks for an `employees` list in the response. If the list has records, it yields that list as one batch; if it is empty, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. This method relies on the base REST connector’s `_get` helper to do the actual HTTP request and returns the extracted records to `paginate`.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches detailed records for individual employees. BambooHR requires a two-step process: first get the directory, then request each employee’s detail page by ID.

**Data flow**: It first reads the employee directory. For each directory row that is a dictionary and has an `id`, it sends another GET request for that employee’s detail endpoint. If BambooHR returns a detail dictionary, the method makes sure it contains the employee ID and yields that one employee as a single-record batch.

**Call relations**: `paginate` calls this when the requested stream is `employees`. It uses the directory endpoint as its starting point, then repeatedly uses the inherited `_get` helper for each employee detail request. Yielding one employee at a time lets the surrounding sync process checkpoint progress more frequently.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches time-off requests, such as vacation or leave records, for a date window. The date window lets an ongoing sync start from its saved cursor instead of always using the beginning of time.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into BambooHR `start` and `end` query parameters using `_date_window_params`, requests the time-off endpoint, then accepts either a raw list response or a response with a `requests` list inside it. If records exist, it yields them as one batch.

**Call relations**: `paginate` calls this for the `time_off_requests` stream. This method calls `_date_window_params` to create the date filter, then uses the inherited `_get` helper to fetch the records and pass them back to the sync flow.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches timesheet entries for a date window. It is similar to the time-off fetcher, but it reads BambooHR’s time tracking endpoint.

**Data flow**: It receives the HTTP client and an optional cursor. It converts the cursor into `start` and `end` query parameters, sends a GET request for timesheet entries, and then accepts either a raw list response or a response with an `entries` list. If any entries are present, it yields them as one batch.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. It shares the date-window logic from `_date_window_params` with `_fetch_time_off`, then hands the resulting records back through `paginate` to the runtime.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR’s field catalog, which describes the available employee fields. This helps the system understand what fields BambooHR knows about, not just the employee data itself.

**Data flow**: It sends a GET request to the metadata fields endpoint. It accepts either a raw list response or a response with a `fields` list inside it. If records are found, it yields them as one batch.

**Call relations**: `paginate` calls this when the requested stream is `meta_fields`. The method uses the inherited `_get` helper for the web request, then returns the normalized list of field records to the sync process.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asks BambooHR to build a specific custom employee report and returns its rows. It is useful when the connector wants a chosen set of employee fields in one report-shaped response.

**Data flow**: It builds a request body containing a report title and a fixed list of employee fields, such as name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to BambooHR’s custom reports endpoint. It then reads the `employees` list from the response and yields it as one batch if any rows are present.

**Call relations**: `paginate` calls this for the `custom_reports` stream. Unlike most other fetchers in this file, it uses the inherited `_post` helper because BambooHR creates custom reports through a POST request rather than a plain GET.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This creates the date range parameters BambooHR requires for time-off and timesheet requests. It turns the sync cursor into a BambooHR-friendly start date.

**Data flow**: It receives an optional cursor. If the cursor is missing or blank, it uses `1970-01-01` so a first sync can collect everything. If a cursor is present, it trims it and takes the first ten characters, which matches a `YYYY-MM-DD` date from an ISO-style timestamp. It returns a dictionary with that `start` date and a far-future `end` date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this right before making their requests. It gives both methods the same date-window behavior, so they do not each have to repeat the cursor-to-date conversion.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/providers/deel.py`

`io_transport` · `source sync`

Deel is an external HR platform, and its API returns records in small pages rather than all at once. This file is the adapter that knows which Deel objects to read, where to ask for them, how to move through pages, and how to recover cleanly when access is not allowed.

The file first defines a small helper for building stream descriptions. A stream description says things like: “read contracts,” “use id as the unique key,” and “use updated_at to fetch only recently changed records.” Most Deel streams can be synced incrementally, meaning the connector asks only for records changed after the last saved point. Forms are different: they do not have an update cursor here, so they are fully re-read each run.

The `DeelConnector` class then supplies the practical rules for talking to Deel’s REST API. It builds request parameters with a page size of 100, optionally adds an `updated_after` filter, asks for pages using an `offset`, and extracts the actual records from Deel’s response body. Think of it like reading a long book 100 pages at a time, stopping when the next bundle is smaller than expected.

If Deel replies with “not authorized” or “forbidden,” the connector does not crash the whole sync. It raises `StreamSkipped`, meaning this particular stream is skipped because the connected account lacks the needed permission.

#### Function details

##### `_stream`  (lines 22–36)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a reusable description of one Deel stream, such as contracts or payslips. It keeps the stream list compact and consistent, so each stream has a name, source API object, unique key, and optional update cursor.

**Data flow**: It receives a stream name and optional settings, such as a different Deel API object name or whether the stream is canonical. It fills in sensible defaults, then creates and returns a `StreamSpec`, which is the system’s plain description of what to fetch and how to identify records.

**Call relations**: This function is used while the file is being loaded to build the Deel stream list. Its main handoff is to `StreamSpec.__init__`, which turns the chosen values into the standard stream object that `DeelConnector` later exposes.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 56–60)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function prepares the first set of query parameters for a Deel API request. It always asks for up to 100 records, and it adds an incremental-sync filter when the stream supports one and a saved cursor is available.

**Data flow**: It takes a stream description and an optional cursor value, usually a timestamp from the last successful sync. It creates a parameter dictionary with `limit: 100`; if both a cursor and cursor field exist, it also adds `updated_after` with that cursor. The result is returned to be reused as the base for each paged request.

**Call relations**: `DeelConnector.paginate` calls this before it starts requesting pages. The returned parameters become the stable part of every request, while `paginate` adds the changing `offset` value for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 63–70)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls real record objects out of Deel’s response body. It protects the sync from oddly shaped data by keeping only dictionary-like records and ignoring anything else.

**Data flow**: It receives decoded response data from the API. If the response is a dictionary with a `data` list, it returns only the items in that list that are dictionaries. If the whole response is already a list, it filters that list the same way. If neither shape matches, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each API response arrives. The cleaned list it returns decides what gets yielded to the rest of the sync, and an empty list tells pagination there is nothing more useful to read.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function reads one Deel stream page by page and yields batches of records to the sync system. It is the main loop that turns Deel’s offset-based API into a simple sequence of record lists.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the API path, creates base parameters, then repeatedly adds the current offset and sends a request. Each response is cleaned into records; non-empty batches are yielded outward. The loop stops when no records arrive or when a page contains fewer than 100 records, which means there are no more full pages to fetch. If Deel refuses access with a 401 or 403 status, it changes that low-level HTTP failure into a `StreamSkipped` signal; other HTTP errors are allowed to keep bubbling up.

**Call relations**: During a sync, the source framework calls this method for each Deel stream. It relies on `_initial_params` to set up request filters, then relies on `_extract_records` to turn each API response into usable records. When Deel says the connected account lacks permission, it creates `StreamSkipped` so the wider sync can skip that stream instead of treating it like an unexpected crash.

*Call graph*: calls 3 internal fn (__init__, _extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/providers/rippling.py`

`io_transport` · `source sync / data fetching`

Rippling is an external HR and company-management service. Its API does not send every worker or team in one response; it sends one “page” at a time and includes a `next` link when there is more to fetch. This file teaches the UFO source framework how to walk through those pages safely.

The file defines three readable streams: companies, workers, and teams. Workers and teams can be fetched incrementally, meaning the connector can ask for only records updated after a known time. Companies do not have that kind of cursor here, so they are refreshed from the beginning.

`RipplingConnector` is the main piece. It inherits from `RestConnector`, so it relies on the shared source framework for the actual HTTP request helper and authentication setup. This connector mainly supplies Rippling-specific details: the base API URL, the stream definitions, how to build the first query, how to pull records out of Rippling’s response shape, and how to follow the `next` link.

A useful analogy is a librarian fetching book carts from a back room: this connector asks for the first cart, unloads usable books, checks whether there is another cart waiting, and repeats until there are no more. If Rippling refuses access with a 401 or 403 status, the connector raises `StreamSkipped`, which tells the wider sync system that this stream could not be read because the credentials or permissions are not good enough.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s `next` link into a path the shared REST client can request next. It accepts both full web addresses and already-relative paths.

**Data flow**: It receives a possible `next` link from an API response. If there is no link, it returns nothing, which means pagination should stop. If the link is a full URL, it keeps only the path and query string; if it is already a relative path, it returns it unchanged.

**Call relations**: During pagination, `RipplingConnector.paginate` calls this after each page is fetched. The returned path becomes the next request target, or stops the loop when there is no more data.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first request to a Rippling stream. It always asks for a fixed page size and, when possible, adds an incremental update filter.

**Data flow**: It receives the stream definition and an optional saved cursor value, usually a timestamp from the last sync. It starts with `limit` set to the connector’s page size. If the stream supports a cursor and a cursor was provided, it adds `updatedAfter` so Rippling returns only newer changes. It returns the finished parameter dictionary.

**Call relations**: `RipplingConnector.paginate` calls this once before making the first API request. After the first request, pagination follows Rippling’s `next` links instead of rebuilding these starting parameters.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the actual records inside a Rippling API response. It hides small differences in response shape so the rest of the connector can work with a simple list of dictionaries.

**Data flow**: It receives decoded response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream name, such as `workers` or `teams`; if that is not present, it looks under a generic `data` key. If the whole response is already a list, it uses that. In every case, it keeps only dictionary-shaped records and returns them as a list.

**Call relations**: `RipplingConnector.paginate` calls this for every fetched page. The returned records are the batches yielded to the source-sync runner.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It fetches page after page from Rippling and yields batches of records until the API says there are no more pages.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It builds the starting path and query parameters, requests a page, extracts usable records, yields them if any exist, then reads the response’s `next` link to decide where to go next. If Rippling returns an access refusal status, it turns that into `StreamSkipped`; other HTTP errors are allowed to rise normally.

**Call relations**: The wider source framework calls this when it needs records for one Rippling stream. Inside the loop it uses `_initial_query` to prepare the first request, `_extract_records` to turn each response into records, and `_next_path` to continue through pagination. When permission is missing or the key is invalid, it hands a clear `StreamSkipped` signal back to the caller instead of pretending the stream is empty.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).

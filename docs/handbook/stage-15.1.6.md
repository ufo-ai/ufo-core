# People operations, recruiting, scheduling, and agreement sources  `stage-15.1.6`

This stage is shared behind-the-scenes support for bringing people-related business data into the system. Each file is a connector, like an adapter plug, for a different outside service. The connectors call each service’s web API, meaning its internet-facing data doorway, and turn the results into steady streams of records that the rest of the system can store, search, and recall.

The recruiting connectors cover Ashby, Greenhouse, and Recruitee. They pull details such as candidates, jobs, applications, interviews, offers, departments, and lookup lists, while handling pages of results and any extra nested records. The HR and workforce connectors cover BambooHR, Deel, and Rippling. They fetch employee, company, team, contract, payslip, timesheet, task, and form data. Calendly adds scheduling data, including users, event types, groups, scheduled events, and invitees. DocuSign and PandaDoc bring in agreement data such as envelopes, documents, templates, and contacts. DocuSign also first discovers the correct regional server for the account before syncing.

## Files in this stage

### Recruiting sources
Connectors that stream candidate, job, application, interview, offer, department, and related recruiting records from applicant-tracking systems.

### `extensions/sources/ufo_ext_sources/providers/ashby.py`

`io_transport` · `source sync / request handling`

Ashby exposes its recruiting data through a web API, but the API has a few special rules: list requests are sent as HTTP POST calls, results arrive in pages, and incremental syncing uses a token that means “give me what changed since this point.” This file hides those details behind an AshbyConnector so the larger system can treat Ashby like any other readable source.

The file first defines the available Ashby streams, such as candidates, applications, jobs, users, and metadata lists like departments or sources. A stream is a small description of one kind of object: where to request it, what field identifies each record, and which timestamp can be used to resume later.

During a sync, the connector creates an HTTP client with Ashby’s required authentication. Ashby expects HTTP Basic authentication, where the API key is used like a username and the password is blank. If credentials are already being proxied by a broker transport, the connector leaves that path alone.

Most streams use the same paging loop: send a request with a page size, optionally include the saved sync token, yield any records, then follow Ashby’s next cursor until no more data remains. One stream is different: application criteria evaluations are stored under each application, so the connector first lists applications and then asks for evaluations one application at a time.

#### Function details

##### `_stream`  (lines 28–46)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a StreamSpec, which is the system’s recipe for reading one Ashby object type. It keeps the long stream list compact and consistent, so each stream can say only what is different about it.

**Data flow**: It receives a friendly stream name, an Ashby API path, and optional details like the primary key and cursor field. It fills in common timestamp fields and creates a StreamSpec object. The result is a reusable description that the connector later uses to know where and how to read that stream.

**Call relations**: The stream catalog uses this helper repeatedly while the file is loaded. Each call produces one StreamSpec by calling StreamSpec.__init__, and those specs are collected into ASHBY_STREAMS for AshbyConnector to expose.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client that will talk to Ashby with the right authentication. It matters because Ashby does not use a normal bearer-token header here; it expects the API key encoded as HTTP Basic authentication.

**Data flow**: It receives a base URL and a Credential. If the credential already contains a custom transport, it lets the parent connector create the client unchanged. Otherwise, it reads the API key from the credential’s bearer field, encodes `api_key:` using Base64, puts that into an Authorization header, and returns an HTTP client configured with that header. If no API key is present, it raises an error instead of making unauthenticated requests.

**Call relations**: The source framework calls this when it needs a client for Ashby. This method either delegates back to the shared RestConnector client setup or creates a new Credential carrying the Basic authentication header after using base64.b64encode to format Ashby’s required token.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 92–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s traffic director for reading pages from Ashby. It chooses the normal paging method for most streams and the special per-application method for criteria evaluations.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. If the requested stream is `application_criteria_evaluations`, it ignores the normal stream endpoint flow and yields pages from the application fan-out reader. For every other stream, it passes the stream and cursor into the default pager and yields each page it receives.

**Call relations**: The broader sync engine calls this when it wants records for a stream. This method then hands the work to either AshbyConnector._paginate_application_criteria or AshbyConnector._paginate_default, depending on which kind of Ashby stream is being read.

*Call graph*: calls 2 internal fn (_paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 102–123)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads ordinary Ashby list endpoints page by page. It also uses the previous sync cursor as Ashby’s sync token, so later runs can ask only for changed records when Ashby supports it.

**Data flow**: It starts with a request body containing the page limit. If a saved cursor is available, it adds that as `syncToken`. It sends the request, yields the `results` list when records are present, and checks Ashby’s `moreDataAvailable` flag. When Ashby provides a `nextCursor`, it uses that cursor on the next request. The output is a sequence of record batches; the function stops when Ashby says there is no more data or fails to provide a next cursor.

**Call relations**: AshbyConnector.paginate calls this for all regular streams. It is the standard paging path that turns one stream description into repeated Ashby API requests until that stream’s current batch of data has been fully read.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 125–161)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads application criteria evaluations, which are not available as one simple list. It first finds applications, then asks Ashby for the criteria evaluations attached to each application.

**Data flow**: It pages through `/application.list` to get application records. For each application record with an id, it requests `/application.listCriteriaEvaluations` using that application id. It copies each returned evaluation, makes sure the application id is present on the row, gathers evaluations for that application, and yields them as a batch. It continues through application pages until Ashby reports no more application data or gives no next cursor.

**Call relations**: AshbyConnector.paginate calls this only for the `application_criteria_evaluations` stream. It acts like a small two-step reader: enumerate parent applications first, then fetch and yield the child evaluation records connected to each one.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/greenhouse.py`

`io_transport` · `source sync / API paging`

Greenhouse stores recruiting information such as candidates, jobs, applications, interviews, offers, users, and reference lists behind many API endpoints. This file is the connector that turns those endpoints into named streams the rest of the system can sync. Without it, the system would not know which Greenhouse URLs to call, how to authenticate, how to move through pages of results, or how to deal with child records such as a job's openings or an application's interviews.

The file first defines stream descriptions using StreamSpec objects. A stream description says, in plain terms, “this is a kind of Greenhouse record, this is its ID field, and this is the date field used to continue from the last sync.” Some streams are simple top-level lists. Others are per-parent streams: the connector must first fetch parent records, such as jobs, then fetch children for each parent, such as that job's stages.

The GreenhouseConnector class does the actual reading. It builds an HTTP client with Greenhouse's Basic authentication when the API key is available directly. When asked to paginate a stream, it decides whether to fetch a simple endpoint or walk parent records first. It follows Greenhouse's Link header pagination, which is like following “next page” signs until there are no more. If Greenhouse replies that the key is unauthorized or forbidden, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_stream`  (lines 65–83)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a StreamSpec, which is the connector's small recipe for one Greenhouse stream. It keeps the many stream definitions short and consistent.

**Data flow**: It takes a stream name plus optional details such as the Greenhouse source object, ID field, date fields, and whether the stream is canonical. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses to know what to fetch and how to track progress.

**Call relations**: This helper is used while the module is loaded to build all the Greenhouse stream constants. Its only handoff is to StreamSpec.__init__, which receives the normalized stream settings.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 230–239)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to call Greenhouse, with the right authentication style. Greenhouse expects Basic authentication where the API key is the username and the password is empty.

**Data flow**: It receives a base URL and a resolved credential. First it asks the parent RestConnector to create the standard client. If the credential contains a bearer value, this function replaces the normal bearer-token header with HTTP Basic authentication using that value as the username. It returns the prepared async HTTP client.

**Call relations**: This method customizes the client-creation step inherited from RestConnector. When it needs direct Greenhouse authentication, it creates an httpx.BasicAuth object; otherwise it leaves the base client alone so an external auth proxy can inject authentication.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 242–245)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This chooses the Greenhouse query parameter used for incremental syncing. Most streams use updated_after, but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It checks the special-case lookup table and returns the matching parameter name if one exists; otherwise it returns updated_after.

**Call relations**: GreenhouseConnector.paginate calls this when a stream has a saved cursor and needs to ask Greenhouse only for newer records. The result becomes part of the query parameters sent to the API.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 247–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for a Greenhouse stream. It decides which endpoint or endpoint pattern to use, follows all pages, and yields batches of records to the sync system.

**Data flow**: It receives an HTTP client, a StreamSpec, and an optional cursor value from a previous sync. If the stream is a per-parent stream, it fetches parent records first and then child records for each parent. If it is a simple stream, it builds a request with per_page set to 500 and, when possible, adds a cursor filter such as updated_after. It yields each page of JSON records. If Greenhouse returns 401 or 403, it turns that into StreamSkipped so the run records a skipped stream rather than crashing.

**Call relations**: This function is the dispatch point for reading Greenhouse data. It calls _paginate_per_parent for nested streams, _paginate_link_header for normal paged endpoints, and _cursor_param when it needs the correct incremental-sync parameter. If Greenhouse refuses access, it raises StreamSkipped to hand a clean skip signal back to the broader sync runtime.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 280–287)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This follows Greenhouse's normal page-by-page API pattern. It keeps requesting the next page as long as Greenhouse provides a Link header pointing to one.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It passes those to the shared link-header paging helper with a page size of 500, then yields each returned page of records unchanged.

**Call relations**: GreenhouseConnector.paginate uses this for ordinary streams. GreenhouseConnector._paginate_per_parent also uses it twice: once to read parent pages and again to read each parent's child pages.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 289–310)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams where Greenhouse stores records underneath another record, such as openings under a job or permissions under a user. It also labels each child record with its parent ID so the connection is not lost downstream.

**Data flow**: It receives the parent endpoint path, a child endpoint template, and the name of the field that should store the parent ID. It fetches pages of parents, takes each parent's id, calls the child endpoint for that parent, and adds the parent ID to each child record if that field is not already present. It yields the child pages after stamping them.

**Call relations**: GreenhouseConnector.paginate calls this when the requested stream is listed as per-parent. This function relies on _paginate_link_header for both parent and child API paging, so all Greenhouse pagination rules stay in one place.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/recruitee.py`

`io_transport` · `source sync`

Recruitee is an external hiring platform, so the system needs a small adapter that knows how Recruitee’s web API is laid out. This file is that adapter. It defines which Recruitee lists can be synced: candidates, offers, and departments. Each stream says what API object to read, what field uniquely identifies a row, and whether it is considered a main, or canonical, stream.

The important detail is that Recruitee uses page-number pagination. That means the connector asks for page 1, then page 2, and so on, with up to 100 records at a time, until it reaches a page that is not full. This is like reading a long report one numbered sheet at a time until the last sheet has fewer lines than expected.

The connector does not store credentials itself. The larger runner supplies an authenticated HTTP client, usually through an authorization proxy. The base URL is deliberately empty here because Recruitee URLs include the customer’s company ID. That must be filled in before a real sync runs, so the connector does not accidentally call the wrong tenant.

There is no write path in this file. It only reads data. If Recruitee replies with “unauthorized” or “forbidden,” the connector skips that stream with a clear message instead of crashing the whole sync unexpectedly.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Recruitee stream page by page and yields each batch of records to the sync system. It is used when the system wants to fetch candidates, offers, or departments from Recruitee.

**Data flow**: It receives an authenticated HTTP client, a stream description such as “candidates,” and an optional cursor value, though Recruitee does not use a cursor here. It builds the stream path, asks the shared REST helper to fetch numbered pages of up to 100 records, and yields each returned list of records. If Recruitee responds with a 401 or 403 status, meaning the key is invalid or does not have permission, it turns that into a StreamSkipped error with a helpful explanation; other HTTP errors are passed upward unchanged.

**Call relations**: During a source sync, the broader RestConnector machinery calls this method for each Recruitee stream. The method hands the actual page fetching to the base connector’s page-number helper, then passes each page back to the caller. If access is refused, it creates a StreamSkipped exception so the sync runner can treat that stream as unavailable rather than as successfully empty.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce sources
Connectors that stream employee, worker, company, contract, payroll, timesheet, task, form, and team records from HR and workforce platforms.

### `extensions/sources/ufo_ext_sources/providers/bamboohr.py`

`io_transport` · `source sync`

BambooHR exposes employee and HR data through web endpoints, but each endpoint returns data in its own shape. This file is the adapter that turns those different BambooHR responses into a steady stream of record batches the rest of the sync system can understand.

The connector declares several readable streams, such as the employee directory, detailed employee records, time-off requests, timesheet entries, metadata fields, and a custom employee report. BambooHR does not use normal page-by-page pagination here. Instead, most endpoints return all matching records at once. The connector’s `paginate` method acts like a traffic director: it looks at the requested stream name and sends the work to the right fetch method.

Authentication is also BambooHR-specific. BambooHR expects HTTP Basic authentication, where the API key is used as the username and the password is literally `x`. The connector also sets `Accept: application/json` because BambooHR may otherwise return XML, which this sync path is not expecting.

For time-based endpoints, BambooHR requires a start and end date. The connector builds that date window from the saved cursor, so later syncs can start from where earlier ones left off. If BambooHR refuses access with a 401 or 403 status, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: This helper creates a `StreamSpec`, which is the system’s small description of one readable BambooHR stream. It keeps the stream declarations short and consistent.

**Data flow**: It receives a stream name and optional details such as the BambooHR object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a `StreamSpec` object that the sync framework can use to know what this stream is called and how records should be identified.

**Call relations**: This function is used while the file is being loaded to build the `BAMBOOHR_STREAMS` list. Each returned `StreamSpec` later reaches `BambooHRConnector.paginate`, which uses the stream name to choose the correct BambooHR fetch path.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 71–86)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to BambooHR. It applies BambooHR’s required headers, timeouts, tenant-specific base URL, and authentication rules.

**Data flow**: It receives a base URL and a resolved credential. It trims the base URL, prepares JSON headers, creates timeout settings, and then chooses how to authenticate: either by using a provided proxy transport unchanged, or by turning a direct API key into BambooHR-style Basic authentication. It returns an `httpx.AsyncClient`, which is an asynchronous web client used for making requests without blocking the whole program. If no usable credential is present, it raises an error.

**Call relations**: The broader REST connector framework calls this when it needs a live client for a BambooHR sync. The client it returns is then passed into `paginate` and the lower-level fetch methods so they can make actual BambooHR API calls.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 88–123)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading BambooHR streams. Given a stream request, it chooses the matching BambooHR endpoint workflow and yields batches of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name and delegates to the matching fetch method, yielding each batch that method produces. If BambooHR returns 401 or 403, meaning the key is invalid or lacks permission, it turns that into `StreamSkipped` with a helpful explanation. Other HTTP errors are allowed to bubble up.

**Call relations**: The sync framework calls this whenever it wants records for a BambooHR stream. `paginate` then calls `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports` depending on the stream name. It is the central switchboard for this connector.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 125–131)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR’s employee directory, which is the broad list of employees. It is useful as a simple employee list and also as the starting point for fetching detailed employee records elsewhere.

**Data flow**: It asks BambooHR for `/v1/employees/directory`. BambooHR returns a response object, and this method reads the `employees` list inside it. If the list has records, it yields that list as one batch; if it is empty, it yields nothing.

**Call relations**: `BambooHRConnector.paginate` calls this when the requested stream is `employees_directory`. The yielded batch goes straight back through `paginate` to the sync framework.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 133–149)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches full detail for each employee. BambooHR does not provide that as one normal list here, so the connector first reads the directory and then asks for each employee one by one.

**Data flow**: It first gets `/v1/employees/directory` and reads the employee rows. For each directory row that is a dictionary and has an `id`, it requests `/v1/employees/{id}`. If the returned detail is a dictionary, it makes sure the `id` is present and yields that one detailed employee record as its own batch.

**Call relations**: `BambooHRConnector.paginate` calls this for the `employees` stream. This method depends on the directory endpoint as its map of employee IDs, then sends each detail record back in small batches so the sync can checkpoint progress frequently.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 151–158)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR time-off requests within a date range. The range is based on the sync cursor so repeated runs do not have to start from scratch unless no cursor exists.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into BambooHR `start` and `end` date parameters by calling `_date_window_params`, then requests `/v1/time_off/requests/`. BambooHR may return either a list directly or an object containing `requests`; this method accepts both shapes and yields the records if any are present.

**Call relations**: `BambooHRConnector.paginate` calls this when the stream is `time_off_requests`. Before making the API call, it hands cursor interpretation to `_date_window_params`; after fetching, it passes the resulting records back through `paginate`.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 160–167)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches timesheet entries from BambooHR within a date range. It supports incremental syncing by using the saved cursor as the next start date.

**Data flow**: It receives the HTTP client and an optional cursor. It calls `_date_window_params` to create the required `start` and `end` parameters, then requests `/v1/time_tracking/timesheet_entries`. If BambooHR returns a list, it uses that; if BambooHR returns an object, it reads the `entries` list. It yields one batch if records exist.

**Call relations**: `BambooHRConnector.paginate` calls this for the `timesheet_entries` stream. It relies on `_date_window_params` for the date window, then returns matching timesheet records to the sync flow.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 169–175)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches BambooHR’s field catalog, which describes available employee fields. That helps the system understand what fields BambooHR knows about, not just employee values themselves.

**Data flow**: It requests `/v1/meta/fields`. BambooHR may return a list directly or an object containing `fields`; this method supports both shapes. If it finds records, it yields them as one batch.

**Call relations**: `BambooHRConnector.paginate` calls this when the requested stream is `meta_fields`. The fetched field definitions are yielded back through the normal stream path.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 177–199)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asks BambooHR to generate a custom report with a specific set of employee fields. It is a way to collect a curated employee table that BambooHR returns under a report endpoint.

**Data flow**: It builds a JSON request body containing a report title and a fixed list of fields such as employee ID, name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to `/v1/reports/custom`, then reads the returned `employees` list. If there are rows, it yields them as one batch.

**Call relations**: `BambooHRConnector.paginate` calls this for the `custom_reports` stream. Unlike the simple GET-based fetch methods, this method submits a report request first, then gives the returned employee rows back to the sync framework.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 202–212)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: This converts the sync cursor into the date range BambooHR requires for time-off and timesheet endpoints. It gives fresh syncs a very early start date so they collect all historical records.

**Data flow**: It receives an optional cursor. If the cursor is present and not blank, it takes the first 10 characters, which correspond to a `YYYY-MM-DD` date in an ISO-style timestamp. If no cursor is present, it uses `1970-01-01`. It always returns a dictionary with `start` and a far-future `end` date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this before contacting BambooHR. It keeps the date-window rule in one place so both time-based streams use the same cursor behavior.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/providers/deel.py`

`io_transport` · `source sync`

Deel is an HR and payroll service. This connector is the adapter that lets this project pull Deel data into recallable pages, meaning records that can later be searched or reused by the system. Without this file, the system would not know which Deel objects exist, which ones can be synced incrementally, or how to walk through Deel’s paginated API responses.

The file first defines a small helper, `_stream`, that creates stream descriptions. A stream is one kind of object to fetch, such as `contracts` or `payslips`. Most streams use `updated_at` as a cursor, which means later syncs can ask Deel only for records changed after the last saved time. `forms` does not have that cursor, so it must be fully refreshed each run.

`DeelConnector` then supplies the concrete API details: the connector name, Deel’s base URL, and the list of streams. Its pagination method follows Deel’s standard pattern: request up to 100 records, starting at an offset, yield the records, then move the offset forward. It stops when Deel returns no records or fewer than 100 records, which means there is no next full page. The connector only reads from Deel; it does not write anything back.

#### Function details

##### `_stream`  (lines 21–35)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a description of one Deel data stream, such as contracts or timesheets. It keeps the stream list short and consistent, so each stream gets the same default choices unless it needs something different.

**Data flow**: It receives a stream name and optional details like the API object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then creates and returns a `StreamSpec`, which is the system’s recipe for fetching that kind of record.

**Call relations**: This function is used while the file is being loaded to build the `DEEL_STREAMS` list. It hands each completed stream recipe to the connector class, which later uses those recipes during syncing.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 55–59)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function prepares the first set of query parameters for a Deel API request. It makes sure every request asks for the standard page size, and it adds an incremental-sync filter when possible.

**Data flow**: It receives a stream description and an optional cursor value, which is usually a saved timestamp from the previous sync. It starts with `limit: 100`; if both a cursor and a cursor field exist, it adds `updated_after` with that cursor. It returns the parameter dictionary that will be copied and expanded for each page request.

**Call relations**: `DeelConnector.paginate` calls this before making paged API requests. The returned parameters become the base settings for every page, while `paginate` adds the changing offset value.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 62–69)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: This function pulls usable record objects out of Deel’s API response. It protects the rest of the connector from odd or empty response shapes by returning only dictionary-like records.

**Data flow**: It receives raw response data, which may be a dictionary containing a `data` list or may already be a list. It filters that list so only dictionary records remain. If the shape is not recognized, it returns an empty list.

**Call relations**: `DeelConnector.paginate` calls this after each API request. If it returns records, `paginate` yields them onward; if it returns nothing, `paginate` treats the stream as finished.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 71–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read loop for one Deel stream. It repeatedly asks Deel for the next page of records and yields each batch to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the API path, prepares base parameters, then starts at offset zero. For each loop, it requests one page, extracts records from the response, yields the records if any exist, and either stops or advances the offset by 100. The output is an asynchronous sequence of record batches.

**Call relations**: During a sync, the broader REST connector machinery calls this method for a specific stream. Inside the loop it relies on `_initial_params` to set up request filters and `_extract_records` to turn raw API responses into clean batches. It then hands those batches back to the syncing pipeline.

*Call graph*: calls 2 internal fn (_extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/providers/rippling.py`

`io_transport` · `source sync`

Rippling is an external HR system, and its API does not send every worker or team in one big response. It sends a page of results and, when more data exists, a “next” link to the following page. This file is the connector that knows how to follow those links safely.

It declares three streams: companies, workers, and teams. A stream is a named kind of data the system can sync. Workers and teams can be synced incrementally, meaning the connector can ask “only give me records updated after this time.” Companies do not have that cursor, so they are read as a full refresh.

The main class, `RipplingConnector`, supplies Rippling’s base web address and the list of streams. Its helper methods build the first request, pull useful records out of the different response shapes Rippling may return, and convert a “next” link into a path the HTTP client can request. The main `paginate` method then loops through pages: request, extract rows, yield a batch, follow the next link.

A key behavior is how it treats refusal errors. If Rippling returns 401 or 403, meaning the token is invalid or lacks permission, the stream is skipped with a clear message instead of being mistaken for a normal empty result.

#### Function details

##### `RipplingConnector._next_path`  (lines 52–63)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s “next page” link into a request path the connector can use. It accepts both full web addresses and already-relative paths, because APIs may return either form.

**Data flow**: It receives a possible next-link string. If there is no link, it returns nothing, which means pagination is finished. If the link is a full URL, it keeps only the path and query string, such as `/workers?page=2`; if it is already a relative path, it returns it unchanged.

**Call relations**: `RipplingConnector.paginate` calls this after each page is downloaded. The result tells the pagination loop whether to ask Rippling for another page or stop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 66–70)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for the first API request in a stream. It sets the page size and, when possible, adds the “updated after” value used for incremental syncing.

**Data flow**: It receives the stream description and an optional saved cursor, which is usually a timestamp from the previous sync. It creates a parameter map with `limit` set to the connector’s page size. If the stream supports a cursor and a cursor value was provided, it adds `updatedAfter` so Rippling returns only newer changed records.

**Call relations**: `RipplingConnector.paginate` calls this before making the first request. After that first request, pagination follows Rippling’s next links instead of rebuilding these starting parameters.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 73–83)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the actual list of records inside Rippling’s response body. It protects the rest of the connector from small differences in how Rippling wraps list results.

**Data flow**: It receives the decoded response data and the stream being read. If the response is a dictionary, it first looks for a list under the stream name, such as `workers`, then under a generic `data` key. If the response itself is a list, it uses that. In all cases, it keeps only items that are dictionaries, because those are valid record-shaped objects, and returns them as a list.

**Call relations**: `RipplingConnector.paginate` calls this for every downloaded page. The records it returns are the batches that `paginate` yields to the wider source-sync runner.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 85–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read loop for a Rippling stream. It downloads one page at a time, yields any records found, and keeps following Rippling’s next-page link until there are no more pages.

**Data flow**: It receives an asynchronous HTTP client, a stream description, and an optional cursor. It starts at the stream’s API path, builds the first query, then repeatedly asks Rippling for data. Each response is turned into records; non-empty record batches are yielded outward. The next-page link from the response becomes the next path. If Rippling refuses access with a 401 or 403 response, it changes that into a `StreamSkipped` message explaining that the credentials or permissions are not sufficient; other HTTP errors are passed upward unchanged.

**Call relations**: The source-sync framework calls this when it needs to read a Rippling stream. Inside the loop it relies on `_initial_query` to prepare the first request, `_extract_records` to pull rows from each response, and `_next_path` to decide where to go next. It hands batches of records back to the runner as they arrive instead of waiting for the whole stream to finish.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).


### Scheduling sources
Connectors that stream users, event types, groups, scheduled events, and invitees from scheduling systems.

### `extensions/sources/ufo_ext_sources/providers/calendly.py`

`io_transport` · `source sync`

This file is a read-only connector for Calendly. Its job is to visit Calendly’s web API, collect useful scheduling data, and shape that data into records the wider system can understand. Without it, the project would not know where Calendly data lives, how Calendly pages through long lists, or how to keep future syncs from rereading everything from scratch.

The connector first asks Calendly who the current API user is. Calendly data is tied to an organization, so most streams need the user’s current organization before they can fetch anything else. If Calendly does not provide that organization, the connector skips those streams rather than guessing.

For normal collections, it reads pages of up to 100 records at a time. Calendly gives a “next page token,” which works like a ticket for the next batch in a long line. Some streams also use a saved cursor, meaning a remembered point in time, so later syncs can ask only for records updated or created after that point.

Invitees are a special case. Calendly lists invitees under each scheduled event, so the connector first fetches events, extracts each event’s ID from its URI, then fetches that event’s invitees. Finally, `flatten` makes selected fields easier to find, such as names, emails, titles, start times, and locations.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID out of a Calendly URI. It is used when the connector needs the short event identifier required by Calendly’s invitee endpoint.

**Data flow**: It receives a value that might be a URI. If the value is a non-empty string, it removes any trailing slash and returns the text after the last slash. If the input is missing or not a string, it returns nothing.

**Call relations**: When invitees are being synced, `CalendlyConnector._invitees` calls this helper for each scheduled event. The returned ID is then used to build the API path for that event’s invitees.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the account behind the current access token. The rest of the connector uses this user record to discover the current organization.

**Data flow**: It receives an HTTP client that already knows how to talk to Calendly. It requests `/users/me`, looks for the `resource` object in the response, and returns that object if it is a dictionary. If the response does not have the expected shape, it returns an empty dictionary.

**Call relations**: `CalendlyConnector.paginate` calls this directly for the `api_user` stream. `CalendlyConnector._org_stream` also calls it before fetching organization-scoped streams, because those streams need the user’s current organization URI.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Calendly collection a page at a time. It hides Calendly’s pagination details so the rest of the connector can simply loop over batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base connector to follow Calendly’s `pagination.next_page_token`, requesting up to 100 records per page from the response’s `collection` field. It yields each page as a list of record dictionaries.

**Call relations**: `CalendlyConnector._org_stream` uses this for organization-level collections such as groups and scheduled events. `CalendlyConnector._invitees` uses it again for each scheduled event’s invitee list.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches a Calendly stream that belongs to the current organization. It makes sure every request includes the organization Calendly expects.

**Data flow**: It starts with an HTTP client, an API path, and optionally a saved cursor plus the Calendly parameter name that should receive that cursor. It gets the current user, reads that user’s `current_organization`, and stops the stream if no organization is available. Then it fetches paged records for that organization and adds organization context to each page before yielding it.

**Call relations**: `CalendlyConnector.paginate` uses this as the main path for event types, groups, memberships, and scheduled events. `CalendlyConnector._invitees` also uses it first to get scheduled events before looking up invitees. It hands the actual page fetching to `CalendlyConnector._paginate_collection` and adds context with `with_context`.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This collects invitees for scheduled Calendly events. Calendly does not provide all invitees as one simple organization list, so this function walks through events first and then asks for invitees event by event.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches scheduled events for the current organization, extracts each event’s UUID from its URI, and requests that event’s invitees. If a cursor is present, it keeps only invitees whose `created_at` value is newer than the cursor. For each non-empty batch, it adds the parent event URI and UUID as context and yields the invitees.

**Call relations**: `CalendlyConnector.paginate` calls this when syncing the `event_invitees` stream. This function relies on `CalendlyConnector._org_stream` to find events, `_uuid_from_uri` to get the event ID, `CalendlyConnector._paginate_collection` to read invitee pages, and `with_context` to remember which event each invitee came from.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing point for reading Calendly streams. Given a stream name, it chooses the correct Calendly endpoint and returns records in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For `api_user`, it returns the current user as a single-record page. For organization streams, it calls the organization-aware reader with the right path and, where useful, passes the cursor as Calendly’s `updated_since` or `min_start_time` parameter. For invitees, it delegates to the special event-by-event invitee reader. If the stream is unknown, it skips it with a clear message.

**Call relations**: The wider source-sync system calls this when it wants records for a particular Calendly stream. This function then hands work to `CalendlyConnector._current_user`, `CalendlyConnector._org_stream`, or `CalendlyConnector._invitees`, depending on what kind of Calendly data is being requested.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into easier-to-use records for storage and search. It keeps the original data but copies important fields into common names such as `name`, `email`, `title`, `start_at`, and `location`.

**Data flow**: It receives one record and the stream it came from. Depending on the stream, it may pull useful values from nested data, such as the user inside an organization membership or the location inside a scheduled event. It returns a new dictionary with the cleaned or highlighted fields added, and for memberships it removes the nested user object so unrelated profile changes do not make the membership look changed.

**Call relations**: After `CalendlyConnector.paginate` has produced raw Calendly records, the connector framework can call this function to prepare each record for the rest of the system. It uses `dict_or_empty` when reading nested user data so missing or malformed user fields do not break the membership flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### Agreement sources
Connectors that stream envelopes, documents, templates, contacts, and related agreement records from document-signing and proposal platforms.

### `extensions/sources/ufo_ext_sources/providers/docusign.py`

`io_transport` · `source sync`

DocuSign stores signed-document activity in “envelopes” and reusable signing setups in “templates.” This connector lets the wider source-sync system pull those items into UFO as read-only records. Without it, a connected DocuSign account would not contribute its signing history or templates to the project’s memory.

The file defines two DocuSign streams: envelopes, which can be synced incrementally using their status-changed time, and templates, which are listed without a cursor. Before it can fetch either stream, the connector asks DocuSign’s shared identity service for user account information. That response says which account is available and what regional base address should be used for actual REST API calls. This is like asking a company receptionist which branch office holds your files before going there.

The connector is careful not to guess when a login has several usable accounts but no single default. In that case it raises a clear fault, because silently choosing the wrong company account could import the wrong envelopes.

Once it has the account base URL, it pages through results 100 at a time. For envelopes, it supplies a starting date and asks DocuSign to include recipients and custom fields, so each remembered envelope contains useful context. If DocuSign refuses access with an authorization error, the stream is skipped with an explanatory message rather than crashing as an unknown failure.

#### Function details

##### `DocuSignConnector._account_base`  (lines 79–109)

```
async def _account_base(self, client: httpx.AsyncClient) -> str
```

**Purpose**: This function finds the exact DocuSign account-and-region API address that should be used for data reads. It prevents the system from accidentally syncing the wrong DocuSign account when a login has multiple accounts.

**Data flow**: It takes an HTTP client that can make authenticated requests, then asks DocuSign’s user-info endpoint for the accounts attached to the grant. It filters that response down to accounts that have both an account ID and a base URI. If there is one usable account, it chooses it; if there are several, it chooses the single account marked as default. If no safe choice exists, it raises a clear stream fault. When successful, it returns a full REST base URL ending in the chosen account ID.

**Call relations**: The paging function calls this first, before asking for envelopes or templates. It uses `list_or_empty` to safely treat missing or oddly shaped account lists as empty lists, and it raises `StreamFault` when the connector cannot safely decide which account to read from.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `DocuSignConnector.paginate`  (lines 111–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches DocuSign records in pages for one stream, such as envelopes or templates. It is the main read loop that turns DocuSign list API responses into batches the sync system can consume.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor value from the previous sync. First it looks up the DocuSign endpoint for that stream and asks `_account_base` for the correct regional account URL. Then it requests records 100 at a time, increasing the start position after each full page. For envelopes, it adds the cursor or an old default date, includes recipient and custom-field details, and asks for ascending order. Each non-empty page is yielded outward as a list of record dictionaries. When DocuSign returns fewer than 100 records, the function stops because it has reached the end. If DocuSign returns a 401 or 403 refusal, it raises `StreamSkipped` with a human-readable reason.

**Call relations**: The source-sync framework calls this when it needs records for a DocuSign stream. Inside the flow, it depends on `_account_base` to know where to send account-specific API calls, and it uses `list_or_empty` to safely pull the record list out of DocuSign’s response. If access is denied, it hands the situation back as a skipped stream rather than treating it as a normal page.

*Call graph*: calls 2 internal fn (__init__, _account_base); 1 external calls (list_or_empty).


##### `DocuSignConnector.render`  (lines 145–154)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function decides how a fetched DocuSign record should look when stored as a recallable page. It gives envelopes a useful title based on the email subject recipients actually saw.

**Data flow**: It receives one DocuSign record and the stream it came from. If the record is an envelope and has a non-empty `emailSubject`, it returns that subject as the page title and a text body containing a small heading plus the full record encoded as sorted JSON. For all other cases, such as templates or envelopes without a subject, it falls back to the parent connector’s normal rendering behavior.

**Call relations**: The sync system uses this after records have been fetched, when it needs to convert raw provider data into a page-like form. This function does not call other connector functions; its only special work is formatting envelope data with `json.dumps` so the full record remains available in a readable stored body.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/pandadoc.py`

`io_transport` · `source sync runs`

This connector is the bridge between UFO and PandaDoc. Without it, the system would not know which PandaDoc API addresses to call, how PandaDoc expects authentication, how to move through paged results, or how to fetch the extra detail that makes documents useful to recall.

The file defines three streams: documents, templates, and contacts. A stream is a named flow of records from an outside service. Documents get special treatment because PandaDoc can return only recently changed documents when given a saved time marker, so syncs do not need to reread everything every time. Each document list row is also expanded with a second details call, because the list response does not include the richer information people are likely to search for, such as fields, tokens, pricing, and recipients.

The connector also protects the sync from common failures. If the whole stream is refused because the key cannot read it, the stream is skipped with a clear message rather than crashing everything. If one document appears in the list but cannot be opened, that document is kept in its simpler list form so one bad record does not ruin the run. PandaDoc uses an `API-Key` authorization scheme, so this file also adapts the normal client setup to send the key in the form PandaDoc expects.

#### Function details

##### `_stream`  (lines 37–51)

```
def _stream(name: str, *, cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description of one PandaDoc stream, such as documents or contacts. The rest of the sync system uses that description to know the stream’s name, its record identifier, and which date fields represent creation or update times.

**Data flow**: It receives a stream name and optional settings, such as which field should act as the update cursor and whether the stream is a main searchable stream. It packages those choices into a `StreamSpec`, which is a small stream-definition object used by the source framework.

**Call relations**: At file load time, this helper is used to build the PandaDoc stream list. It hands each completed stream definition to the connector class through `PANDADOC_STREAMS`, so later sync code can ask the connector what PandaDoc data is available.

*Call graph*: 1 external calls (__init__).


##### `PandaDocConnector._make_client`  (lines 66–72)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to PandaDoc and makes sure manually supplied PandaDoc API keys are sent in PandaDoc’s required format. It matters because PandaDoc does not use the more common `Bearer` wording for this kind of key.

**Data flow**: It receives a base API address and a resolved credential. It first asks the base REST connector to create the normal web client. If the credential contains a bearer-style key value, it changes the client’s `Authorization` header to `API-Key <key>`. It returns the ready-to-use client.

**Call relations**: This fits into the setup step before any PandaDoc requests are made. The broader REST connector machinery asks for a client, and this method customizes that client just enough for PandaDoc while leaving broker-provided authentication alone when the transport already injects credentials.


##### `PandaDocConnector.paginate`  (lines 74–103)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one PandaDoc stream page by page. It knows the PandaDoc list endpoints, adds the right paging parameters, applies incremental document filtering when possible, and yields batches of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It chooses the matching PandaDoc API path, requests pages of up to 100 records, and reads the `results` array safely as a list. For documents, it adds sorting and `modified_from` filtering, then enriches each document through `_details`. It yields each non-empty batch. When the page is shorter than the page size, it stops because PandaDoc has no more records. If PandaDoc refuses access with a 401 or 403 status, it turns that into a `StreamSkipped` result with a human-readable reason.

**Call relations**: This is the main read loop the source framework calls when it is time to sync a PandaDoc stream. During the loop, it calls `list_or_empty` to normalize the response shape and calls `PandaDocConnector._details` for each document so document records contain more than just the list summary. If PandaDoc refuses the stream, it reports that as a skipped stream instead of letting the whole sync fail.

*Call graph*: calls 2 internal fn (__init__, _details); 1 external calls (list_or_empty).


##### `PandaDocConnector._details`  (lines 105–117)

```
async def _details(self, client: httpx.AsyncClient, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This function fetches the richer detail record for a single PandaDoc document. It turns a basic list entry into a fuller document record that includes the information users are more likely to recognize and search for.

**Data flow**: It receives the HTTP client and one document record from a list page. It looks for a usable string `id`. If there is no valid id, it returns the original record unchanged. Otherwise it requests `/public/v1/documents/{id}/details`. If PandaDoc says the document is forbidden or missing, it returns the original list record. If details are available, it combines the list record and the details response into one dictionary and returns that richer record.

**Call relations**: This function is called by `PandaDocConnector.paginate` only for the documents stream. It acts like a second pass after listing: pagination finds the document shells, and `_details` fills in the extra contents when PandaDoc allows it.

*Call graph*: called by 1 (paginate).

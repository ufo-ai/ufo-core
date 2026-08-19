# Brokered and keyed connector backends  `stage-13.1`

This stage is shared behind-the-scenes support for connecting the agent to outside tools and accounts. It is the “switchboard” that lets the rest of the system ask for a tool without needing to know where the user’s secret login token is kept.

The Composio files form one backend: the client talks to Composio’s API, the broker presents Composio tools in the system’s normal connector shape, the proxy forwards ordinary web requests through Composio, the resolver chooses an allowed Composio toolkit by name, and the MCP session helper makes one tool call over MCP, a standard way for services to expose tools. The Pipedream files do the same kind of job for Pipedream Connect: define safe connected accounts, discover actions, run them, and proxy requests without exposing secrets. The connector objects file shows connected accounts as workspace items that can be shared, revoked, or disconnected with permission checks. Keyed connectors cover simpler services that use API keys. The MCP extension discovers tools from configured MCP servers. The evaluation manifest supplies fake mailbox, calendar, and code-search connectors for repeatable tests.

## Files in this stage

### Composio backend
Composio integration discovers tools, routes dynamic providers, executes tool calls, and proxies provider HTTP access without exposing user secrets.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling for connector discovery, execution, file transfer, and credential proxying`

ComposioBroker is the shared doorway every Composio-backed connector uses. Think of it like a front desk: the rest of the system asks for “tools for Gmail” or “run this Slack action,” and this file translates that request into Composio API calls, then returns the result in the project’s own simple shapes.

The broker is intentionally stateless. It fetches the Composio client each time a method runs, so tests can swap in a fake transport and long-lived connections do not leak across calls.

It supports several main jobs. It can list available tools and fetch a single tool’s schema, rewriting Composio’s file-upload fields into the system’s own “workspace file” vocabulary. It can execute a tool for a workspace-specific broker user. If execution fails because the tool slug is wrong, it tries to include real available slugs in the error so the next attempt can improve. If execution fails because an old or missing connected account is being used, it gives reconnect guidance instead of pretending the tool name was the problem.

It also finds file outputs buried inside tool responses, creates upload slots for tool inputs, searches tools through Composio’s tool router, and builds a credential object whose HTTP transport proxies through Composio. That last part matters because provider tokens stay with Composio; this system only receives a safe proxy path.

#### Function details

##### `ComposioBroker.tools`  (lines 48–49)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds tools offered by a provider, optionally narrowed by a search query. It returns them in the project’s standard BrokerTool form so callers do not need to understand Composio’s raw catalog format.

**Data flow**: It receives a workspace id, provider name, and query text. It asks the current Composio client for matching tools, then passes the returned rows through _discovered_tools, which keeps usable slugs, short descriptions, and input schemas. The result is a tuple of BrokerTool objects.

**Call relations**: This is the quick discovery path used when the connector layer needs to show or choose available provider tools. It gets the Composio client for the current call, then hands the raw Composio list to _discovered_tools to normalize it.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 51–64)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the detailed input schema for one tool slug. This tells the rest of the system what arguments the tool expects before trying to run it.

**Data flow**: It receives a workspace id, provider name, and tool slug. It asks Composio for that tool’s schema, converts any file-upload fields into this system’s workspace-file format, and returns a BrokerTool with the slug, description, and input schema. If Composio says the slug does not exist, it raises UnknownBrokerTool so the caller knows the requested tool is invalid.

**Call relations**: The connector layer calls this when it needs exact instructions for one tool. It relies on the Composio client for the raw schema and on workspace_file_schema to translate Composio’s file input shape into the form used by dynamic connector tools.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 66–89)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for a workspace and connected account. It also turns two common confusing failures into more helpful errors: stale accounts get reconnect guidance, and missing tool slugs can get a list of real alternatives.

**Data flow**: It receives the workspace id, provider, tool slug, arguments, connected account id, and an optional idempotency key, which is a repeat-safe request marker. It builds the Composio broker-user id from the workspace id, sends the execution request, and returns Composio’s response dictionary. If Composio reports an error, it checks whether the account looks stale, whether the slug was missing, or whether the error should simply be re-raised.

**Call relations**: This is the main run path for provider actions. It calls _stale_account to recognize dead connected accounts, _reconnect_error to explain what the user should do next, and _slug_miss to enrich a missing-tool error with useful tool names.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 91–96)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a tool run from anywhere inside the response. This lets later parts of the system download or present generated files without knowing the exact nesting of Composio’s response.

**Data flow**: It receives the full tool response dictionary. It creates an empty list, asks _collect_files to walk through the nested response, and returns the found files as BrokerFile objects in a tuple. It does not modify the response.

**Call relations**: This is called after tool execution when the connector layer wants to know whether the result contains downloadable files. The actual recursive search is delegated to _collect_files.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 98–114)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a temporary upload destination for a file that will be passed into a Composio tool. This lets the system upload bytes to Composio’s file store first, then give the tool a small reference to that file.

**Data flow**: It receives the workspace id, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot and gets back a URL plus a storage key. It returns a StagedUpload containing the URL to upload to, the content type to use, and the argument object that should later be placed in the tool call.

**Call relations**: This is used before executing a tool that needs a file input. It relies on the Composio client to mint the upload slot, then packages Composio’s upload details into the project’s StagedUpload shape.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 116–119)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for relevant connector tools using Composio’s tool router, which is a search service for matching a query to tools. It gives the system a richer search path than a simple catalog list.

**Data flow**: It receives a workspace id, provider name, and query text. It gets the current Composio client and passes everything to search_connector_tools. The returned BrokerSearch result is passed back unchanged.

**Call relations**: The connector layer uses this when it wants search-style discovery rather than a plain provider listing. This method is mostly a clean adapter: it supplies the current client and workspace context to Composio’s search helper.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 121–137)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Builds a safe credential object for making provider HTTP calls through Composio’s proxy. It first confirms that the connected account belongs to this workspace’s broker user, which prevents one workspace from accidentally using another workspace’s account.

**Data flow**: It receives the workspace id, provider, and connected account id. It builds the expected broker-user id, asks Composio to confirm the account is connected for that user and provider, and then returns a Credential whose transport sends requests through ComposioProxyTransport. If the account is missing, it raises a reconnect-style error instead of returning a credential.

**Call relations**: This is used when a connector needs HTTP access to the provider but should not receive the provider’s secret token. It calls _reconnect_error for missing accounts, constructs ComposioProxyTransport for proxied HTTP, and wraps that transport in the project’s Credential object.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 139–160)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by trying to include real tool slugs for the provider. This helps an agent recover from a bad tool name instead of blindly repeating the same mistake.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the bad slug into search words, asks Composio for nearby tools, and if needed falls back to listing broader provider tools. If useful tools are found, it returns a new ComposioError whose message includes their slugs; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a missing tool slug. It uses _discovered_tools to normalize candidate tools before adding their names to the error message.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 163–172)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through a nested response and collects objects that look like Composio file outputs. A file output is recognized by the presence of a non-empty presigned URL plus a name and MIME type.

**Data flow**: It receives any value and a list being used as the collection basket. If the value is a matching file-shaped dictionary, it appends a BrokerFile with the file name and URL. If the value is a dictionary or list, it searches each child value; other values are ignored.

**Call relations**: ComposioBroker.file_outputs starts this search after a tool run. _collect_files does the detailed walking so file_outputs can offer a simple public method.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 175–186)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error likely means the connected account is no longer valid for this broker. This distinction matters because a stale account should lead to reconnect instructions, not a misleading list of tool names.

**Data flow**: It receives a Composio error and the account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account: Composio’s own “connected account not found” wording or the specific account id plus “not found.” It returns true if those signs are present, otherwise false.

**Call relations**: ComposioBroker.execute uses this before treating a 404 as a missing tool slug. That ordering keeps old account grants from being mistaken for bad tool names.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 189–190)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Adds plain reconnect guidance to a Composio error. It keeps the original status and body, but appends advice telling the user to reconnect the provider account.

**Data flow**: It receives the original Composio error and provider name. It asks stale_grant_guidance for provider-specific reconnect wording, combines that with the original error body, and returns a new ComposioError with the same status.

**Call relations**: ComposioBroker.execute calls this when a run appears to use a stale account. ComposioBroker.credential calls it when the requested connected account cannot be confirmed for this workspace.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 193–213)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio’s raw tool-list rows into the project’s BrokerTool objects. It filters out rows without a usable slug and trims long descriptions so discovery results stay compact.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses the slug from the slug field or name field, skips invalid rows, shortens the description if needed, converts the input parameters into workspace-file-aware schema form, and returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal catalog discovery. ComposioBroker._slug_miss also uses it when building a helpful error message after a bad tool slug.

*Call graph*: called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling and connector discovery/execution`

Composio acts like a switchboard for external services such as GitHub and many others. Instead of this project hard-coding every provider’s tools and holding every user’s access token, it asks Composio what tools exist and tells Composio to run the chosen tool on the user’s connected account. This file is the bridge to that switchboard.

The main class, ComposioClient, wraps Composio’s web API. It can create an OAuth connection link, check that a returned account really belongs to the expected workspace user, list available tools, fetch a tool’s input shape, run a tool, and create temporary upload slots when a tool needs a file. It also opens Tool Router sessions, which are Composio-hosted search sessions used to find the right tool for a plain-language task.

The file also decides which Composio toolkits are safe and useful to offer. A toolkit must have Composio-managed authentication, must actually contain tools, and must not be on the project’s manually banned list. This prevents users from connecting services that would appear available but cannot complete meaningful work.

A few helper functions keep the rest of the project insulated from Composio’s raw response shapes. They turn API replies into simple project objects, rewrite file-upload fields into workspace-file paths the agent understands, and raise clear errors when Composio answers with a failure or an unexpected shape.

#### Function details

##### `connectable`  (lines 122–146)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit should be offered to users through this project. It filters out banned toolkits, toolkits that cannot use Composio-managed login, and toolkits that have no tools to run.

**Data flow**: It receives a toolkit slug and a catalog record from Composio. It checks the slug against the local banned list, then reads the record to see whether managed authentication schemes exist and whether the tool count is greater than zero. It returns true only when all checks pass.

**Call relations**: ComposioClient.connectable_toolkit uses this when checking one specific toolkit slug, and ComposioClient.list_toolkits uses it while searching the catalog. In both paths, it is the local gatekeeper before a connector is shown or claimed.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 153–156)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception when Composio fails or sends data this project cannot safely use. It preserves both the HTTP status code and the response body so callers can tell what went wrong.

**Data flow**: It receives a numeric status and a text body. It builds a readable error message, stores the status on the error object, and stores the original body for later inspection.

**Call relations**: Many client methods raise this when a required field is missing, an account is not valid, or an API response is an error. The shared _body helper also raises it for failed or malformed HTTP responses.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 173–182)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to connect an outside service through Composio. This is the start of the consent flow, similar to clicking “Connect Google” or “Connect GitHub.”

**Data flow**: It takes a toolkit name, a broker user id, and a callback URL. It first gets or creates an auth configuration for that toolkit, then posts those details to Composio’s connected-account link endpoint. It returns the redirect URL Composio provides, or raises an error if no usable URL comes back.

**Call relations**: It calls _auth_config to find the login setup, then _post to ask Composio for the hosted connection link. If Composio’s reply is missing the link, it raises ComposioError so the connection process fails loudly instead of giving the user a broken flow.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 184–208)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Verifies that a completed Composio connection is the right account for the right workspace and toolkit. This stops one user’s or one service’s connected account from being accepted in the wrong place.

**Data flow**: It receives a connected account id, the expected user id, and the expected toolkit slug. It fetches the account from Composio, checks its owner, checks that its status is active, checks that it belongs to the expected toolkit, and then returns a small OAuthAccount object containing the account id.

**Call relations**: It relies on _get to read account metadata and raises ComposioError for ownership, status, or toolkit mismatches. It hands back an OAuthAccount to the rest of the connector system only after those safety checks pass.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.account_label`  (lines 210–213)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Fetches a human-friendly label for a connected account, if Composio has one. This can be used to show users which account they connected.

**Data flow**: It receives an account id, fetches that account from Composio, and reads the alias field. It returns the alias when it is a non-empty string, otherwise it returns nothing.

**Call relations**: It uses _get for the actual API call. Unlike the stricter account verification path, this is a small convenience lookup for display text.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 215–245)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists tools available in a Composio toolkit, optionally filtered by a search query. It follows Composio’s paged results so tools beyond the first page are not missed.

**Data flow**: It receives a toolkit slug and an optional query. It repeatedly asks Composio for pages of tools, keeps only dictionary-like tool rows, appends them to a growing list, and stops at the last page or at the project’s maximum listing size. It returns the collected rows as an immutable tuple.

**Call relations**: It calls _get for each page of /tools results. ComposioBroker._slug_miss uses it when a requested tool slug is not already known and the broker needs to look through the toolkit catalog.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 247–248)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full schema for one Composio tool. A schema describes what inputs the tool expects and what it is for.

**Data flow**: It receives a tool slug, performs a GET request for that specific tool, and returns Composio’s response as a dictionary.

**Call relations**: It is a thin wrapper around _get. Other code can call it when it needs the exact input description for a known tool rather than a search result.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 250–267)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a single toolkit slug is valid, safe to look up, and connectable through this deployment. It returns the display name if the toolkit can be offered to users.

**Data flow**: It receives a slug. It first rejects strings with unsafe characters, then fetches the toolkit record from Composio, treats a 404 as “not found,” applies the local connectable rules, and finally returns the toolkit’s name or the slug as a fallback.

**Call relations**: It uses _get to read the toolkit details and connectable to apply the local eligibility rules. It is the one-toolkit version of catalog discovery and protects the URL path from user-supplied slashes or dots.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 269–287)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio’s toolkit catalog and returns only the services this deployment is willing and able to broker. This powers open-ended connector discovery.

**Data flow**: It receives a search query and result limit. It asks Composio for matching toolkits, walks through the returned items, skips malformed or non-connectable records, and returns pairs of toolkit slug and display label.

**Call relations**: It calls _get to search /toolkits and calls connectable on each result. This keeps the user-facing discovery list aligned with the same rules used when claiming an individual toolkit.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 289–303)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio’s server side for a broker user, optionally tied to a specific connected account. This is where an actual external-service action happens.

**Data flow**: It receives the tool slug, tool arguments, user id, optional connected account id, and optional idempotency key. It builds a JSON request body, refuses it if it is too large, adds an idempotency header when provided, posts to Composio’s execute endpoint, and returns the response dictionary.

**Call relations**: It uses _post for the API call and json.dumps to measure the outgoing payload size. It deliberately executes through Composio rather than through the Tool Router, keeping account tokens and execution metering on Composio’s execute API.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 305–329)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file that a tool will later use. This lets the sandbox upload bytes directly to Composio’s storage without this client carrying the file contents.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts those details to Composio’s upload-request endpoint, checks that a storage key is present, then returns a ComposioUpload containing the key and, when needed, a presigned PUT URL.

**Call relations**: It calls _post to mint the upload slot and raises ComposioError if the reply is missing the storage key or contains a malformed upload URL. It constructs ComposioUpload so later tool arguments can refer to the staged file key.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 331–342)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Starts a Composio Tool Router session for semantic tool search. The Tool Router is a Composio service that can search tools by use case rather than exact tool name.

**Data flow**: It receives a user id and a list of toolkit slugs. It posts a session request to Composio, reads the session id and MCP URL from the response, validates that both exist, and returns a ToolRouterSession with those values.

**Call relations**: search_connector_tools calls this when no cached session exists for a user and connector. The returned URL is later used to call the COMPOSIO_SEARCH_TOOLS tool through the MCP session helper.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 344–362)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication setup that should be used for a toolkit, creating a Composio-managed one if none exists. This is needed before a user can be sent through the connection flow.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config, extracts an id if one is present, and returns it. If none exists, it posts a request to create a managed auth config, checks that the created record has an id, and returns that id.

**Call relations**: ComposioClient.connect_link calls this before creating a connection link. It uses _get, _post, and _auth_config_id, and raises ComposioError if Composio creates something without an id.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 364–366)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a GET request to Composio and turns the response into a plain dictionary. It is the shared read path for the client.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the GET request, passes the response to _body, and returns the parsed dictionary that _body accepts.

**Call relations**: Most read-style methods call this, including account checks, toolkit lookup, toolkit search, tool listing, and schema lookup. It delegates connection setup to _http and response validation to _body.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_auth_config, account_label, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 368–372)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a POST request to Composio and turns the response into a plain dictionary. It is the shared write/action path for the client.

**Data flow**: It receives an API path, a JSON body, and optional headers. It opens an HTTP client, sends the POST request, passes the response to _body, and returns the parsed dictionary that _body accepts.

**Call relations**: Methods that create links, create auth configs, execute tools, create upload slots, and start Tool Router sessions call this. Like _get, it keeps HTTP setup and response checking in one place.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 374–380)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the temporary HTTP client used for one Composio API call. It attaches the base URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the ComposioClient’s API key and optional transport setting. It creates and returns an httpx asynchronous client configured for Composio’s API.

**Call relations**: _get and _post call this whenever they need to contact Composio. Because each call gets its own client context, connections are closed cleanly and tests can swap in a mock transport.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 383–391)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Validates and parses an HTTP response from Composio. It turns successful JSON object replies into dictionaries and turns failures or unexpected shapes into ComposioError.

**Data flow**: It receives an httpx response. If the status code is an error, it raises ComposioError with the response text. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is an object-like dictionary.

**Call relations**: _get and _post both send every response through this helper. It is the common safety check that prevents callers from quietly accepting error pages, arrays, or malformed payloads.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 394–420)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input fields into the project’s simpler workspace-file format. This lets the agent provide a file path from the workspace instead of constructing Composio’s internal storage reference itself.

**Data flow**: It receives any piece of a schema. If it finds a dictionary marked as file-uploadable, it replaces that portion with an object that asks for a workspace_file path. If it sees nested dictionaries or lists, it recursively rewrites their contents. Other values pass through unchanged.

**Call relations**: _search_result calls this while turning Tool Router search results into BrokerTool entries. It hides Composio’s raw upload details from the model-facing tool schema.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 423–430)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small helper for the auth setup lookup.

**Data flow**: It receives a response dictionary. It looks for an items list, scans it for the first dictionary with a string id, and returns that id. If the expected shape is absent, it returns nothing.

**Call relations**: ComposioClient._auth_config calls this after asking Composio for existing auth configs. If it returns an id, _auth_config can reuse the existing setup instead of creating a new one.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 433–440)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the deployment’s ComposioClient from the COMPOSIO_API_KEY environment variable. It fails immediately if the key is missing, because connector login cannot work without it.

**Data flow**: It reads COMPOSIO_API_KEY from the process environment. If the value is missing or empty, it raises a RuntimeError. Otherwise it returns a ComposioClient configured with that key.

**Call relations**: Other parts of the extension can call this when they need the standard production client. It centralizes the rule that the Composio broker key must come from the environment.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 447–448)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. It avoids repeated type-checking in search-result parsing.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: _search_result calls this many times while reading nested Tool Router data. It makes malformed or missing nested fields behave like empty objects rather than crashing the parser.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 451–454)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Extracts clean strings from a list and returns them as an immutable tuple. It is used for Tool Router fields that should be lists of text.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only non-empty strings and returns them as a tuple.

**Call relations**: _search_result uses this to read tool slugs, plan steps, guidance, and pitfalls from the Tool Router response without trusting every element blindly.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 457–491)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Turns a raw Composio Tool Router search response into the project’s BrokerSearch object. The result contains matched tools plus useful planning notes for the agent.

**Data flow**: It receives a response dictionary from the Tool Router. It finds tool schemas and search results, gathers primary and related tool slugs without duplicates, builds BrokerTool objects with descriptions and rewritten input schemas, and collects recommended plan steps, guidance, and pitfalls. It returns one BrokerSearch containing all of that.

**Call relations**: search_connector_tools calls this after the MCP tool call returns. It uses _dict, _str_tuple, and workspace_file_schema to safely reshape Composio’s response into the broker format used by the rest of the project.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 494–517)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for useful tools inside one connector using Composio’s semantic Tool Router. This lets the agent ask for tools by describing the task, not by already knowing exact tool slugs.

**Data flow**: It receives a ComposioClient, workspace id, connector slug, and query text. It turns the workspace id into a Composio broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the COMPOSIO_SEARCH_TOOLS tool over MCP, and converts the raw result into BrokerSearch.

**Call relations**: It calls ComposioClient.tool_router_session only when the session cache does not already have one, then uses mcp_session.mcp_call_tool to perform the search. Finally it hands the response to _search_result so callers get the project’s normal search object.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

Composio's Tool Router can search for tools through an MCP endpoint. MCP, or Model Context Protocol, is a standard way for an app to talk to external tools and services. This file is the small bridge that opens that connection, asks for one tool call, and normalizes the answer.

The main problem it solves is that MCP replies can come back in a few different forms. Sometimes the useful answer is already parsed as structured data. Sometimes it is in a separate structured-content field. Sometimes it is just a block of text that happens to contain JSON. Without this file, every caller would need to repeat the same careful unpacking logic and might misread valid results.

The flow is like calling a help desk and then translating whatever format they answer in into one standard form. The function opens a temporary HTTP-based MCP session using FastMCP, calls the named tool with the given arguments and headers, then closes the session. After that, it checks the result in order of most reliable to least: parsed data first, structured content second, JSON text third, and finally a fallback wrapper. Importantly, this is only for searching via the Tool Router; actual tool execution is kept elsewhere so Composio's normal execution and metering path remains in charge.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: This asynchronous function calls one named tool on a remote Composio MCP endpoint and returns the answer as a plain dictionary. It is used when the project needs the Tool Router's search result without exposing the rest of the code to the details of the MCP client response.

**Data flow**: It receives an endpoint URL, a tool name, tool arguments, HTTP headers, and a timeout. It opens a FastMCP streamable-HTTP connection to that endpoint, sends the tool call, and receives a result object. It then looks for a dictionary in the result's parsed data, then in its structured content, then tries to parse the first text block as JSON. The output is always a dictionary, either the real structured result or a small wrapper such as text or result when the response is less structured.

**Call relations**: When some higher-level Composio code needs one Tool Router MCP call, this function is the piece that actually talks over the wire. It builds a StreamableHttpTransport for the endpoint, gives that transport to fastmcp.Client to run the session, asks the client to call the requested tool, and uses json.loads only if it has to turn a text reply into structured data.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Many connectors expect to talk directly to a provider, such as Google Sheets or another API. But with Composio, the project is not allowed to hold the provider's secret token. Composio keeps that credential and injects it on the server side. This file is the adapter that makes that arrangement feel normal to the rest of the code.

The main piece, ComposioProxyTransport, acts like an HTTP transport, which is the layer that actually sends requests. When a connector tries to call a provider URL, this transport reads the method, URL, headers, and body, removes unsafe or irrelevant headers like authorization and content-length, and packages the request into a POST to Composio's proxy endpoint. Composio then performs the provider request using the connected account's stored credential.

When Composio replies, the transport rebuilds the provider's status code, headers, and body so the connector can keep working as if it had called the provider directly. It also protects shared proxy processes from huge responses by optionally stopping reads after a size limit. If Composio reports a non-JSON binary file, the file is not copied through this proxy. Instead, the code returns a temporary redirect pointing to where the bytes can be downloaded. ComposioRequestForwarder uses the same transport for one-off broker forwarding, with a hard timeout so a slow or stuck backend cannot freeze the proxy.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 66–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request rewrite step. It takes an ordinary provider HTTP request and sends it to Composio's proxy endpoint instead, so Composio can add the hidden credential and call the provider safely.

**Data flow**: It starts with an incoming HTTP request: method, full URL, headers, body, and any timeout setting. It reads the body, copies safe headers into Composio's expected parameter format, includes the connected account id, and builds a new POST request to Composio. After the inner transport sends that proxy request, it reads the response with a size guard. If Composio itself failed, it returns that failure as-is; otherwise it converts Composio's payload back into a provider-style response.

**Call relations**: This is called whenever this transport is used to send a provider request. It relies on _read_bounded to safely collect Composio's response body, then hands successful proxy payloads to _provider_response so the rest of the connector sees a normal HTTP response.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the whole response body from Composio, with an optional maximum size. The limit prevents a shared proxy process from using too much memory if a provider returns a very large response.

**Data flow**: It receives an HTTP response from the Composio proxy call. If no maximum size is set, it simply reads all bytes. If a maximum is set, it reads chunk by chunk, adds each chunk to a buffer, and checks the total size. If the response grows past the limit, it closes the response and raises a Composio error; otherwise it returns the collected bytes.

**Call relations**: handle_async_request uses this right after the proxy call completes. It is the safety gate before any response body is decoded or turned back into a provider response.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio's proxy result into the response shape a connector expects from the original provider. It preserves useful provider details like status, headers, and JSON bodies, while handling Composio's special binary-file case.

**Data flow**: It receives a decoded Composio payload and the original provider request. First it unwraps nested data envelopes until it reaches the actual provider result. It reads the provider status and headers, dropping body-specific headers that would no longer be trustworthy after reconstruction. If the payload points to binary data, it returns a 302 redirect with a location header to the stored file. Otherwise it converts dictionaries and lists back into JSON bytes, strings into UTF-8 bytes, and missing data into an empty body, then returns an HTTP response.

**Call relations**: handle_async_request calls this after a successful Composio proxy response has been read. It is the final translation step that lets downstream connector code continue as if it had spoken directly to the provider.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport. It is used to release network resources when the proxy transport is no longer needed.

**Data flow**: It has no input beyond the transport object itself. It forwards the close request to the inner transport, which cleans up its own connections and related resources. Nothing is returned.

**Call relations**: Code that creates a ComposioProxyTransport can call this during cleanup. ComposioRequestForwarder.forward does so in a finally block, ensuring the transport is closed even if forwarding fails or times out.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a granted account, mainly for the broker or command-line forwarding path. It wraps the same proxy behavior in a single call with a response size cap and a hard timeout.

**Data flow**: It receives an account id, HTTP method, URL, headers, and raw request body. It gets the Composio client and API key, builds a ComposioProxyTransport for that account, creates an HTTP request with a timeout, and sends it through the transport. It reads the returned body, turns the result into a ForwardedResponse with status, headers, and bytes, and always closes the transport afterward. If the whole operation takes too long, it raises a Composio timeout error.

**Call relations**: This is the one-shot forwarding entry within this file. Instead of a connector using ComposioProxyTransport directly, the broker calls forward when it needs to execute a single outbound provider request through Composio and return the result to its caller.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect flow`

Composio offers many third-party toolkits, and this project does not want to list each one as a separate built-in connector. This resolver creates an “open namespace”: if a user asks for a provider slug, such as a short service name, the resolver decides whether Composio can supply it.

The file’s main class, ComposioResolver, is intentionally small and mostly stateless. It keeps only a shared ConnectorBroker, which is the object that later performs the real connection and tool execution work. For each request, it asks the Composio client for fresh information, so tests or runtime configuration changes can swap the client behavior without stale connections hanging around.

The flow is simple. First, claims rejects any locally banned provider names. If not banned, it asks Composio’s live catalog whether the toolkit can be connected. If accepted, descriptor builds the OAuth description used for the connection. OAuth is the common “sign in and grant access” flow; here the host is left empty because Composio keeps the account token and runs tools on its side. entry creates the connector record that routes the provider name to the shared broker. catalog powers discovery by searching Composio’s toolkit list and returning only connectable services. transfer_hosts names Composio file-store hosts that are allowed for file inputs and outputs.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-store hosts are trusted for moving files in and out of tool runs. It matters because tool results or inputs may include files, and those files still need to pass through the project’s sandbox rules.

**Data flow**: It takes no extra input beyond the resolver object. It reads the fixed COMPOSIO_TRANSFER_HOSTS list from the Composio client module and returns it as a tuple of host names; it does not change anything.

**Call relations**: When the connector system needs to know what external file locations a Composio-backed grant may use, it asks this property. The answer is handed back directly from the shared Composio constants, so the resolver stays in sync with the transport rules used elsewhere.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function decides whether a provider name belongs to Composio’s open connector namespace. It first blocks names that are banned locally, then checks Composio’s live catalog to see whether the requested toolkit is actually connectable.

**Data flow**: It receives a provider slug as text. It lowercases the slug and compares it with the local banned set; if it is banned, the answer is immediately false. Otherwise it creates or retrieves the current Composio client with composio_client(), asks that client whether the toolkit can be connected, and returns true only when Composio confirms a matching toolkit.

**Call relations**: During connector selection, registered providers get a chance first, and this resolver is used for the remaining open Composio names. claims is the gatekeeper in that story: it calls the Composio client only after the local ban check, so clearly disallowed names are rejected without a network catalog lookup.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth connection description for a provider that Composio has accepted. The description tells the surrounding connector system how the user should authorize access, while leaving token custody with Composio.

**Data flow**: It receives the provider slug. It creates a ComposioOAuthProvider using that slug and an empty host value, then returns that provider description. It does not contact Composio or modify the resolver.

**Call relations**: After claims has established that a provider can be served by Composio, the connect flow can ask descriptor for the authorization shape. descriptor hands the work to ComposioOAuthProvider, which packages the provider name in the form expected by the broader connector framework.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the connector entry that routes a provider slug to the shared Composio broker. It also turns the slug into a human-friendly label, so names with underscores look nicer in user-facing lists.

**Data flow**: It receives the provider slug. It builds a label by replacing underscores with spaces and title-casing the words, then creates a ConnectorEntry containing the original provider slug, the label, and this resolver’s shared broker. The returned entry is ready for the connector registry to use.

**Call relations**: Once the system decides that Composio owns a requested provider, entry turns that decision into a concrete connector record. It calls ConnectorEntry to package the provider and broker together, so later tool execution can be routed through the single ComposioBroker rather than a separate broker per toolkit.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–52)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: This function searches Composio’s toolkit catalog for connectable services. It supports discovery tools, so users can find services they are actually able to connect rather than seeing unusable names.

**Data flow**: It receives a search query and an optional maximum number of results. It gets the current Composio client with composio_client(), asks it for matching toolkits, then turns each returned slug and label pair into a CatalogEntry. It returns all entries as an immutable tuple and does not change the resolver.

**Call relations**: When a discovery feature needs suggestions, catalog is the resolver’s search path into Composio. It delegates the lookup to the live Composio client, then wraps each result with CatalogEntry so the rest of the connector system receives a consistent catalog format.

*Call graph*: 2 external calls (__init__, composio_client).


### Pipedream backend
Pipedream Connect integration discovers actions, validates connected accounts, runs tools, and proxies provider calls through Pipedream.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Pipedream offers many ready-made actions, such as sending an email or creating a record in another service. UFO needs to present those actions in its own connector format, so an agent can choose one, fill in safe inputs, and run it using a member’s connected account. This file is that translator.

The central class, PipedreamBroker, is deliberately stateless. Each method asks for a fresh Pipedream client when it runs. That matters because tests or deployments may swap the network transport, and the broker should always honor the current setup rather than keeping an old connection around.

For discovery, the broker asks Pipedream for actions belonging to one app and turns them into BrokerTool objects. It also converts Pipedream’s configurable properties into a JSON schema, which is a machine-readable description of what arguments are allowed. It hides internal fields, especially the connected-account slot, because the broker fills that in itself.

For execution, it fetches the action definition, injects the account binding, checks that the account really belongs to the requested provider, and calls Pipedream’s server-side run API. It turns common stale-account failures into clearer reconnect guidance. For files, it reads Pipedream’s File Stash output and returns download URLs. Direct staged uploads are refused because Pipedream actions expect file URLs instead.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–61)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for a given provider and turns them into UFO broker tools. This is used when the system wants to know what actions are available for an app, optionally filtered by a search query.

**Data flow**: It receives a workspace id, provider name, and query text. It looks up the provider’s Pipedream app slug, asks the current Pipedream client for matching actions, then converts those raw action records into BrokerTool objects. The result is a tuple of tools the agent can inspect or use.

**Call relations**: This is the discovery path for Pipedream tools. PipedreamBroker.search calls it when a search response is needed, and it delegates the provider lookup to _spec and the conversion of raw Pipedream rows to _listed_tools.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 63–69)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one specific Pipedream action. It tells the rest of the system what arguments the action accepts, without exposing fields that the broker must fill in itself.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts the configurable properties, converts those properties into a JSON-style input schema, and returns a BrokerTool with the slug, description, and schema.

**Call relations**: This is used when the system already knows the action key and needs a precise form for its inputs. It relies on _definition to fetch Pipedream’s action metadata, then uses _props, _input_schema, and _str to shape that metadata into UFO’s tool format.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 71–106)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It also protects against running the action with the wrong app account and gives clearer guidance when the saved account grant has gone stale.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, account id, and optional idempotency key. It fetches the action definition, copies the arguments, inserts the connected account into the action’s app slot, verifies the account belongs to the requested app, and asks Pipedream to run the action. It returns the action response, or raises a detailed PipedreamError if the action key is unknown, the account is wrong, the grant is stale, or the action reports an error.

**Call relations**: This is the main run path. It calls _definition first; if the action is missing, it asks _key_miss to produce a more helpful not-found error. It uses _app_slot to find where the account belongs, _spec to confirm the provider’s app, and _stale_account plus _reconnect_error to turn certain failures into reconnect instructions.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 108–126)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Pipedream action and presents them as downloadable broker files. This lets the sandbox fetch output files without needing to know Pipedream’s internal response shape.

**Data flow**: It receives the full action response. It looks inside the response’s exports for File Stash upload entries, keeps only entries with a valid download URL, derives a friendly file name from the local path when available, and returns BrokerFile objects. It does not modify the response.

**Call relations**: This runs after an action response is available. It does not call the Pipedream API; it only translates the response’s File Stash metadata into the connector system’s file output format.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 128–140)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Refuses the normal staged-upload flow for Pipedream actions. Pipedream expects file inputs to be URLs, so this function points callers toward sharing a workspace file and passing its download link instead.

**Data flow**: It receives details about a file the caller wants to upload, such as filename, mimetype, and checksum. Instead of creating an upload target, it immediately raises a ValueError explaining the correct approach. Nothing is uploaded or changed.

**Call relations**: This is called only if the broader connector system tries to prepare a staged file upload for a Pipedream action. It intentionally stops that path because Pipedream’s action model uses URL-based file inputs.


##### `PipedreamBroker.search`  (lines 142–143)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a search result containing Pipedream tools. Pipedream does not provide a separate planning or routing layer here, so the search result is simply the matching tools.

**Data flow**: It receives the workspace, provider, and query. It asks PipedreamBroker.tools for matching tools and wraps them in a BrokerSearch object. The output is a search response suitable for the connector system.

**Call relations**: This is a thin wrapper around the tools discovery flow. It calls PipedreamBroker.tools and then packages the result as BrokerSearch.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 145–166)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a proxy credential for using a connected Pipedream account. It first confirms that the account exists in the workspace and belongs to the requested provider.

**Data flow**: It receives a workspace id, provider name, and account id. It looks up the provider spec, fetches the connected account from Pipedream, rejects missing accounts with reconnect guidance, rejects accounts tied to a different app, and returns a Credential containing a PipedreamProxyTransport. That transport is what later HTTP calls use to act through the connected account.

**Call relations**: This is used when the system needs a live credential rather than just running a catalog action. It depends on _spec to know the expected app and _reconnect_error to explain missing stale grants. It builds PipedreamProxyTransport around the current Pipedream client transport.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 168–176)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition for one Pipedream action. It turns Pipedream’s not-found response into UFO’s UnknownBrokerTool signal.

**Data flow**: It receives an action slug. It asks the current Pipedream client for that action’s definition, unwraps the data field when Pipedream returns one, and returns the definition dictionary. If Pipedream says the action does not exist, it raises UnknownBrokerTool instead.

**Call relations**: PipedreamBroker.schema uses this to describe an action, and PipedreamBroker.execute uses it before running an action. It is the shared fetch-and-normalize step for action metadata.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 178–192)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when an action key is unknown during execution. Instead of only saying “not found,” it tries to include the real available action keys for that provider.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider’s app, asks Pipedream for that app’s actions, converts them into tools, and creates a PipedreamError that names the missing action and available alternatives. If the catalog lookup fails, it still returns a simpler not-found error.

**Call relations**: PipedreamBroker.execute calls this after _definition reports an unknown tool. It uses _spec to find the app and _listed_tools to produce a readable list of valid action slugs.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 195–202)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream error looks like a stale connected account grant. A stale grant means the stored account reference no longer works and the member probably needs to reconnect.

**Data flow**: It receives a PipedreamError and the account id being used. It lowercases the error body and looks for narrow signs such as “external user not found” or the same account id appearing with “not found.” It returns true only for those likely stale-account cases.

**Call relations**: PipedreamBroker.execute uses this after run failures and action-level errors. When it returns true, execute asks _reconnect_error to add member-friendly reconnect guidance.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 205–206)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds clear reconnect instructions to an existing Pipedream error. This helps the agent tell the member what to do when an old account grant no longer works.

**Data flow**: It receives a PipedreamError and provider name. It keeps the original status code, appends standard stale-grant guidance for that provider to the error body, and returns a new PipedreamError.

**Call relations**: PipedreamBroker.execute uses this for stale run failures, and PipedreamBroker.credential uses it when a workspace account cannot be found. It relies on the shared stale_grant_guidance helper for the wording.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 209–213)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up UFO’s registered Pipedream connector information for a provider name. This is how the broker knows which Pipedream app slug belongs to a provider.

**Data flow**: It receives a provider string. It searches the registered Pipedream connectors map and returns the ConnectorSpec when found. If the provider is not registered, it raises a KeyError.

**Call relations**: This is a small but important lookup used across discovery, execution, credential creation, and missing-key error reporting. PipedreamBroker.tools, PipedreamBroker.execute, PipedreamBroker.credential, and PipedreamBroker._key_miss all depend on it before talking about a provider’s app.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 216–232)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw Pipedream action-list entries into UFO BrokerTool objects. It filters out unusable entries and gives each tool a slug, description, and input schema.

**Data flow**: It receives a tuple of dictionaries from Pipedream’s action listing. For each item with a non-empty string key, it extracts a description, reads configurable properties, converts those properties into an input schema, and adds a BrokerTool to the output tuple.

**Call relations**: PipedreamBroker.tools uses this for normal tool discovery. PipedreamBroker._key_miss also uses it when building a helpful list of valid action keys after an execution request names an unknown action.

*Call graph*: calls 3 internal fn (_input_schema, _props, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 235–237)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the list of configurable properties from a Pipedream action definition or listing item. These properties describe the inputs and special internal slots of an action.

**Data flow**: It receives a definition dictionary. It reads configurable_props, keeps only entries that are dictionaries, and returns them as a list. If the field is missing or not a list, it returns an empty list.

**Call relations**: PipedreamBroker.schema and _listed_tools use this before building input schemas. _app_slot also uses it to find the special connected-account slot needed during execution.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 240–247)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream input field where the connected account must be placed. Without this slot, the broker cannot safely run the action on behalf of the member.

**Data flow**: It receives an action definition and slug. It scans the action’s properties for the app-type property with a valid name and returns that name. If no such property exists, it raises a PipedreamError saying the action cannot bind an account.

**Call relations**: PipedreamBroker.execute calls this just before running an action. The returned field name is where execute inserts the account’s authProvisionId.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 250–273)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Turns Pipedream action properties into a JSON schema for the arguments an agent may provide. It hides broker-owned and Pipedream-internal fields so the model is not asked to fill in things it should not control.

**Data flow**: It receives a list of property dictionaries. It skips unnamed fields, the app account slot, directory fields, and internal fields whose type starts with $. For the remaining fields, it maps Pipedream property types to simple JSON types, copies a description when available, tracks required fields, and returns an object schema.

**Call relations**: PipedreamBroker.schema uses this for a single action definition, and _listed_tools uses it while listing many actions. It calls _str to safely read property type text.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 276–277)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. This prevents accidental non-string values from leaking into descriptions or type checks.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It has no side effects.

**Call relations**: PipedreamBroker.schema, _listed_tools, and _input_schema use this as a small cleanup helper when reading Pipedream metadata that may be missing or shaped unexpectedly.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector consent, account lookup, and action execution`

This file is the bridge between this project and Pipedream’s Connect API. Pipedream acts like a trusted valet for OAuth, the web sign-in flow where a user grants access to another app. Instead of storing a Gmail token here, the system stores only a Pipedream connected-account id. When an action runs, Pipedream injects the real credential on its side.

The file starts by naming the supported Pipedream connectors. Right now the allowlist contains Gmail. This matters because not every provider is meant to go through Pipedream; only providers that need this broker belong here.

The main class, PipedreamClient, knows how to ask Pipedream for a project access token, create a hosted consent link, look up connected accounts, list available actions, fetch an action’s definition, and run an action. It uses httpx, an HTTP client library, for network calls.

The most important safety idea is ownership checking. A project-level Pipedream token can read many accounts in the Pipedream project, so this code checks the account’s external user id before using it. That is the “show me this badge belongs to this person” step. Without it, a confused part of the system might accidentally run a tool using someone else’s connected account.

#### Function details

##### `PipedreamError.__init__`  (lines 80–83)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error when Pipedream rejects a request or returns data this client cannot safely use. It keeps the HTTP status code and response body so callers can report or react to the exact failure.

**Data flow**: It receives a numeric status and a text body. It turns those into a readable exception message, then stores both values on the error object for later inspection.

**Call relations**: This error is raised throughout this client when token creation, account lookup, action listing, or response parsing fails. The broker code also raises it when connector setup or execution cannot continue safely.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 121–142)

```
async def access_token(self) -> str
```

**Purpose**: Gets the project-level access token this server needs before calling most Pipedream API endpoints. It reuses a cached token until it is close to expiring, avoiding an unnecessary login request on every call.

**Data flow**: It first checks the process-wide token cache using the client id. If a fresh token is already available, it returns it. Otherwise it sends the client id and secret to Pipedream’s OAuth token endpoint, checks that the response contains an access token, stores the token with its expiry time, and returns the token string.

**Call relations**: The lower-level request helpers, PipedreamClient._get and PipedreamClient._post, call this before making authenticated Pipedream calls. It uses PipedreamClient._http to open the HTTP client and _body to turn the HTTP response into checked data.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 144–159)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and browser link for a user to connect an account. This is the start of the hosted consent flow, where the user is sent to Pipedream to approve access.

**Data flow**: It receives an external user id plus success and error return URLs. It posts those to Pipedream, expects back a token and a connect link URL, and returns them as a ConnectToken object. If either value is missing, it raises an error instead of pretending the consent link is usable.

**Call relations**: This public client method hands off the actual network work to PipedreamClient._post. It raises PipedreamError if Pipedream’s answer is missing the fields the rest of the OAuth flow depends on.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 161–168)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and proves that it belongs to the expected external user. This prevents the project token from being misused to access another user’s account.

**Data flow**: It receives a Pipedream account id and the external user id that should own it. It fetches the account record, unwraps the response if it is inside a data field, and passes the record through an ownership check. The result is a ConnectedAccount object if the account is healthy and owned by the expected user.

**Call relations**: It uses PipedreamClient._get for the API call, _dict to safely treat optional nested data as a dictionary, and _owned_account for the real safety check.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 170–174)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a human-friendly name for a connected account, if Pipedream has one. This is useful for showing users which account they connected, such as a mailbox name.

**Data flow**: It receives an account id, fetches the account record, unwraps the response if needed, and reads the name field. It returns the name when it is a non-empty string, otherwise it returns nothing.

**Call relations**: It uses PipedreamClient._get to retrieve the account and _dict to safely handle Pipedream’s response shape. Unlike the account validation methods, it only extracts a display label.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 176–186)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads a connected account and checks that it belongs to a specific workspace. This is the workspace-level guard before a connector action is allowed to run.

**Data flow**: It receives an account id and a workspace id. It fetches the account, converts the record into a ConnectedAccount, then checks whether the account’s external user id matches the workspace’s allowed id pattern. If the pattern does not match, it raises a permission error; otherwise it returns the account.

**Call relations**: It calls PipedreamClient._get for the account record, _account to validate the basic account fields, and _workspace_owns_external_user to decide whether the workspace owns that external user id.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 188–202)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently connected account for a given external user and app. This is used after a user finishes the consent flow, when the system needs to identify which new account was just created.

**Data flow**: It receives an external user id and a Pipedream app slug. It asks Pipedream for matching accounts, keeps valid dictionary records, chooses the one with the newest created_at value, checks that it has an id, and verifies that it belongs to the same external user. It returns a ConnectedAccount or raises an error if no usable account exists.

**Call relations**: It uses PipedreamClient._get to list accounts, _dict to cleanly handle response entries, and _owned_account to apply the same ownership guard used by direct account reads.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 204–233)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the Pipedream actions available for one app, optionally filtered by a search query. It follows Pipedream’s paging system so discovery can see actions beyond the first page.

**Data flow**: It receives an app slug and optional query text. It repeatedly requests pages of actions, adds valid action records to a growing list, follows the end cursor when another page is available, and stops when a page is short, the cursor is missing, or the maximum listing size is reached. It returns a tuple of action dictionaries.

**Call relations**: The broker’s key-miss path calls this when it needs to search Pipedream’s action catalog. Internally it uses PipedreamClient._get for each page and _dict to safely read paging information.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 235–236)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition for one Pipedream action component. A caller uses this when it needs to know the action’s inputs and structure before running or describing it.

**Data flow**: It receives an action key. It sends a GET request to the matching component endpoint and returns Pipedream’s response as a dictionary.

**Call relations**: This is a thin public wrapper around PipedreamClient._get. The request helper supplies authentication, sends the HTTP request, and checks the response shape.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 238–256)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on the server side using the connected user account identified by the configured properties. It also asks Pipedream to create a fresh file stash so files produced by the action can be returned as downloadable links.

**Data flow**: It receives an action key, an external user id, and configured action inputs. It builds the run request body, adds a fresh stash id, checks that the JSON payload is not too large, and posts it to Pipedream. The returned dictionary is Pipedream’s action result.

**Call relations**: This method hands the network request to PipedreamClient._post. It uses json.dumps only to measure the request size before sending, so oversized tool arguments fail locally instead of being sent to Pipedream.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 258–261)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked dictionary response. It is the shared path for read-style API calls.

**Data flow**: It receives an API path and optional query parameters. It gets an access token, opens an HTTP client with that token, sends the GET request, and turns the response into a dictionary or raises an error.

**Call relations**: Higher-level methods such as account_label, connected_account, workspace_account, newest_account, list_actions, and action_definition call this instead of repeating authentication and response checking themselves.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 6 (account_label, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 263–266)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked dictionary response. It is the shared path for API calls that create something or run something.

**Data flow**: It receives an API path and a request body dictionary. It gets an access token, opens an HTTP client with that token, sends the body as JSON, and parses the checked response into a dictionary.

**Call relations**: PipedreamClient.connect_token uses it to create a Connect token, and PipedreamClient.run_action uses it to execute an action. It relies on access_token, _http, and _body for the common plumbing.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 268–281)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used for talking to Pipedream. For authenticated calls, it adds the bearer token and Pipedream environment header; for the token request, it leaves those headers off.

**Data flow**: It receives an optional token. If a token is present, it builds headers containing authorization and environment information. It then returns an httpx AsyncClient configured with Pipedream’s base URL, timeout, optional test transport, and those headers.

**Call relations**: PipedreamClient.access_token uses it for the unauthenticated OAuth token call. PipedreamClient._get and PipedreamClient._post use it for authenticated API calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 284–285)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This avoids crashes when Pipedream returns a missing or unexpected nested field.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary, giving callers a safe object to read from.

**Call relations**: Several account and listing methods call this before reading nested response data. _account also uses it when reading the app information inside a connected account record.

*Call graph*: called by 6 (account_label, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 288–301)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that an account record is healthy and belongs to one exact external user. This is one of the core security guards in the file.

**Data flow**: It receives a raw account record, the expected account id, and the expected external user id. It first turns the record into a ConnectedAccount through _account, then compares the account’s stored owner with the expected owner. It returns the account if they match, or raises a permission error if they do not.

**Call relations**: PipedreamClient.connected_account and PipedreamClient.newest_account call this whenever they need user-level ownership proof. It delegates basic account validation to _account and raises PipedreamError on mismatch.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 304–316)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Converts a raw Pipedream account record into the project’s small ConnectedAccount object. It also rejects records that are missing an owner or are marked unhealthy.

**Data flow**: It receives a dictionary from Pipedream and the account id being read. It checks for a non-empty external owner, rejects explicitly unhealthy accounts, extracts the app slug if present, and returns a ConnectedAccount with the id, app, and owner.

**Call relations**: PipedreamClient.workspace_account calls this before workspace ownership checking. _owned_account calls it before comparing a record’s owner with the expected external user.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 319–320)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard text prefix used for external user ids that belong to a workspace. This gives the code one consistent way to name Pipedream users created under a workspace.

**Data flow**: It receives a workspace UUID. It turns the UUID into its compact hexadecimal form and combines it with the project’s external-user prefix and a trailing underscore. The result is a string prefix.

**Call relations**: _workspace_owns_external_user uses this prefix to check ownership, and connection_user_id uses it when creating a new state-scoped external user id.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 323–332)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user id belongs to a given workspace. It accepts both an older direct workspace id form and the newer workspace-plus-connection-id form.

**Data flow**: It receives a workspace id and an external user id string. It first checks the direct legacy form. If that does not match, it checks for the workspace prefix, removes it, and verifies that the remaining connection id is exactly the expected length and made only of lowercase hexadecimal characters. It returns true or false.

**Call relations**: PipedreamClient.workspace_account calls this after reading an account to decide whether the workspace is allowed to use it. It relies on workspace_user_prefix for the canonical prefix.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 335–337)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for one workspace and one connection state value. The state is hashed so the final id is predictable for the same state but does not expose the full state text.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, keeps the first 32 hexadecimal characters as the connection id, prefixes that with the workspace user prefix, and returns the final external user id.

**Call relations**: It uses workspace_user_prefix to keep naming consistent with the ownership checker. The resulting id is later used by Pipedream account lookup and ownership checks.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 340–348)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe dictionary, or raises a clear error. It is the central response checker for this client.

**Data flow**: It receives an httpx response. If the status code is an error, it raises PipedreamError with the status and response text. If the response body is empty, it returns an empty dictionary. Otherwise it parses JSON and requires the top-level value to be a dictionary.

**Call relations**: PipedreamClient.access_token, PipedreamClient._get, and PipedreamClient._post all call this after receiving a response. That keeps error handling and response-shape validation consistent across the client.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 351–369)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a PipedreamClient from environment variables. It fails early if the deployment is missing the Pipedream client id, client secret, or project id needed to broker OAuth.

**Data flow**: It reads the required Pipedream settings from the process environment. If any required value is missing, it raises a RuntimeError explaining what must be configured. Otherwise it returns a PipedreamClient, using the configured environment name or the default production environment.

**Call relations**: This is the convenient factory for code that needs the deploy’s real Pipedream client. It calls the PipedreamClient constructor after validating configuration.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and teardown`

Pipedream keeps provider credentials on its own servers, so this project cannot simply attach a Gmail, Slack, or other provider token to outgoing requests. This file solves that by acting like a postal forwarding service. A connector writes a normal HTTP request to the provider, and this transport wraps that request so Pipedream can send it on with the right credential added safely on the server side.

The main class, PipedreamProxyTransport, is an httpx transport. A transport is the low-level part of an HTTP client that actually sends requests. When a request comes in, it first asks the Pipedream client for a Pipedream access token. It then reads the original request body, copies useful headers, and adds a special prefix to those headers so Pipedream knows which ones should be forwarded to the real provider. Headers that belong only to the local HTTP connection, such as host, content length, or authorization, are deliberately left out so they do not confuse or leak into the upstream request.

Next, it encodes the original provider URL into a safe text form and places it inside a Pipedream proxy URL, along with the external user id and account id. Finally, it sends this new request through an inner transport. The response is passed back unchanged, which matters because callers may depend on exact status codes or headers for pagination, retry behavior, or error handling.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the core forwarding step. It receives a normal HTTP request meant for a provider, rewrites it into a Pipedream Connect Proxy request, and sends it so Pipedream can inject the provider credential safely.

**Data flow**: It starts with an incoming httpx request, plus the transport's stored Pipedream client, account id, external user id, and inner transport. It asks the client for a Pipedream access token, reads the request body, filters and re-prefixes headers that should be passed upstream, and base64-url-encodes the original provider URL so it can fit inside the proxy path. It builds a new request to Pipedream's proxy endpoint with the account and user identifiers in the query string, sends that request through the inner transport, and returns the resulting response unchanged.

**Call relations**: In the bigger flow, an httpx AsyncClient uses this method whenever code tries to send a provider request through this custom transport. Inside the method, it uses httpx.Request.aread to collect the body, base64.urlsafe_b64encode to make the target URL safe for the proxy path, httpx.URL to build the Pipedream endpoint, and httpx.Request to create the rewritten request. It then hands the rewritten request to the inner transport, which performs the actual network send.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This shuts down the wrapped inner transport when the proxy transport is no longer needed. It is important for releasing network resources cleanly, such as open connections.

**Data flow**: It takes no new input beyond the transport instance itself. It calls the inner transport's close method and waits for it to finish. Nothing is returned, but the underlying network resources are given a chance to close properly.

**Call relations**: This is used during cleanup, when the HTTP client or surrounding code is finished with the transport. Rather than doing its own shutdown work, it passes the close request directly to the inner transport, because that is the piece that owns the actual connection machinery.


### Connector account objects
Connected accounts and per-agent permissions are exposed as workspace objects with inspection, sharing, revocation, and disconnect controls.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling for workspace object listing, status, apply, and delete operations`

A connector account is not just ordinary data: it represents access to a third-party service, so the system must be careful about who can see it, share it, or remove it. This file turns those accounts into two object types the rest of the workspace can understand. A `connection` is the member-owned account itself, created only through the separate account-connection flow. A `connector_grant` is one agent’s access to that account, like a key issued to one assistant rather than ownership of the whole house.

The file defines small data shapes for these objects, then two object stores. `ConnectionObjects` lets the workspace list connections, inspect their details, show live status, and disconnect them. It refuses to create or edit connections directly, because connecting an account requires third-party consent. `ConnectorGrantObjects` lists and edits agent-specific grants. It can attach an already-held connection to an agent, flip a grant between private and shared, or revoke only that agent’s access.

The important safety rule is that every change goes through the grants service and requires a speaking member when ownership matters. The exported `CONNECTION_OBJECT` and `CONNECTOR_GRANT_OBJECT` register these behaviors with the workspace object system so tools and the portal can use them consistently.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This is part of a simple contract for account summary-like objects. It says any summary used here must be able to provide the name of the outside service, such as a provider name.

**Data flow**: There is no real computation in this property definition. It describes that a summary object must already contain provider text, and code using that object can read the provider from it.

**Call relations**: The `_named` helper relies on this property when it builds stable object names. Any connection or grant summary passed into `_named` is expected to satisfy this contract.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This is the second part of the account summary contract. It says a summary must expose the account identifier used by the external provider.

**Data flow**: There is no runtime transformation here. The property promises that a summary object has an account ID value that other code can read.

**Call relations**: The `_named` helper combines this account ID with the provider to create the object name used by both connection rows and grant rows.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper gives account summaries their workspace object names. It turns a group of summaries into a dictionary keyed by the standard account name, so later code can list objects with predictable names.

**Data flow**: It receives a tuple of summary objects that each expose a provider and account ID. For each summary, it asks `account_object_name` to build the canonical name, then returns a dictionary from that name to the original summary.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this before building visible object rows. It is the shared naming step that keeps connections and grants speaking the same naming language.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–83)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list of connection objects a member-readable object store can show. Each row represents one connected provider account and includes who owns it.

**Data flow**: It asks the grants layer for connection summaries, gives them stable names through `_named`, and turns each summary into an `OwnedRow`. The output is a tuple of rows with a display summary and an owner record containing the owning member and the connection generation ID.

**Call relations**: The workspace object system calls this when it needs to list `connection` objects for a member or in the portal. It relies on `connection_summaries` for the raw facts and `_named` for consistent names.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 85–103)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the detailed object view for one connected account. It is used when someone opens or fetches a specific `connection` object.

**Data flow**: It receives the requested object name and owner information, then looks through current connection summaries for the matching generation ID. If found, it returns an `ObjectDetail` containing the provider, account ID, creation time, and update time; if the connection no longer exists, it returns nothing.

**Call relations**: The object framework calls this after a row has identified a connection. It reads from `connection_summaries` and packages the result as a `ConnectionSpec` so callers see a clean object description rather than raw grant-service data.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 105–118)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This provides live status information for a connection beyond its basic saved fields. It shows who owns the account, what host it belongs to, and which agents currently use it.

**Data flow**: It receives the tool context, object name, and owner record. It finds the matching connection summary by generation ID and returns a small dictionary of status values, or returns nothing if the connection has disappeared.

**Call relations**: The workspace object system calls this when a tool or portal asks for status on a `connection`. It reads the same connection summaries as the list and detail paths, but returns operational information rather than the object spec.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 120–128)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately blocks direct creation or editing of connection objects. A real account connection needs third-party authorization, so users must go through `connect_account` instead.

**Data flow**: It receives the requested connection spec, any old spec, and ownership information, but does not apply them. It immediately raises a `VerbNotSupported` error with a message explaining that account connection must use the proper consent flow.

**Call relations**: The object framework calls this when someone tries to apply changes to a `connection`. Instead of handing off to the grants service, it stops the request so the safer account-connection flow remains the only creation path.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 130–140)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a member-owned provider account. Deleting a `connection` removes the underlying account connection and, by implication, cuts off all agent grants that depended on it.

**Data flow**: It receives the tool context, object name, and owner record. It checks that the grants service is available and that there is a speaking member, then asks the grants service to disconnect the connection generation as that actor. If the disconnect fails because the connection changed or disappeared, it raises an error.

**Call relations**: The object framework calls this for deletion of a `connection`. It is the point where an object delete turns into a real grant-service disconnect, guarded by the owner-or-admin permission gate declared on the class.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 151–152)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This defines the one grant edit that an admin is allowed to perform without being the connection owner: making a shared grant private. It prevents admins from broadening access while still letting them reduce exposure.

**Data flow**: It receives the old grant spec and the requested new spec. It returns true only when the old grant was shared and the requested spec is identical except that `shared` becomes false.

**Call relations**: The member-readable object framework uses this as part of permission checking for `connector_grant` updates. It supports the file’s safety rule: owners may share, while admins may only make access more private.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 154–171)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list of connector grant objects visible through the workspace object system. Each row represents one agent’s access to one connected account and says whether that access is shared or private.

**Data flow**: It asks the grants layer for grant summaries, gives them canonical account-style names through `_named`, and converts each summary into an `OwnedRow`. The owner record includes the member who owns the underlying connection, whether the grant is shared, and the grant generation ID.

**Call relations**: The object framework calls this when listing `connector_grant` objects. It mirrors `ConnectionObjects._member_rows`, but reads `grant_summaries` because it is listing agent access edges rather than the base account connections.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 173–214)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the detailed object view for one connector grant. It tells the caller which provider account the grant uses, whether it is shared, and what agent the grant is scoped to.

**Data flow**: It receives a name and owner record, then searches current grant summaries for the matching generation ID. If found, it builds a `ConnectorGrantSpec` and an `ObjectDetail`; the detail always links to the agent that holds the grant, and for private grants it also links back to the underlying connection object. If the grant no longer exists, it returns nothing.

**Call relations**: The object framework calls this after a grant row is selected or fetched. It uses `grant_summaries` for the raw grant facts and `account_object_name` to point private grants back to their related `connection` object.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 216–230)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This provides live status information for a connector grant. It shows the owning member, host, target agent, and whether the grant is shared.

**Data flow**: It receives the tool context, object name, and owner record. It finds the current grant summary by generation ID and returns a dictionary of status fields, or returns nothing if the grant is gone.

**Call relations**: The object system calls this when a tool or portal asks for status on a `connector_grant`. It complements the detailed object view by exposing current operational facts from `grant_summaries`.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 232–280)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This applies changes to a connector grant. It can attach an existing connection to an agent, or change only the grant’s `shared` setting; it refuses attempts to create a brand-new external account or swap a grant to a different account.

**Data flow**: For a new grant with no old object or owner, it requires a grants service and a speaking member, then asks the grants service to attach the provider account to the current conversation’s agent context with the requested sharing setting. For an existing grant, it reloads the current grant summary, verifies the provider and account ID have not been changed, and if only `shared` changed, asks the grants service to update that setting. It returns nothing on success and raises clear errors if access is unavailable, data changed mid-edit, or the requested action is not allowed.

**Call relations**: The object framework calls this when someone applies a `connector_grant` create or update. It is the main bridge from declarative object edits to the grants service’s attach and share-change operations, while preserving the rule that `connect_account` is the only way to create the underlying account connection.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 282–292)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected account. It leaves the underlying connection, and any other agents’ grants, in place.

**Data flow**: It receives the tool context, object name, and grant owner record. It checks that the grants service exists and that a speaking member is present, then asks the grants service to revoke the grant generation as that actor. If the revoke fails because the grant changed or disappeared, it raises an error.

**Call relations**: The object framework calls this for deletion of a `connector_grant`. It turns the object delete into a grants-service revoke operation, using the revoke permission gate declared on the class.


### Declared connector sources
Additional connector sources cover static API-key-backed services and workspace-configured MCP servers that publish callable tools.

### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / manifest load`

Some external services, like Datadog, do not fit the usual “connect an account through a broker” flow. A workspace member already owns an API key, and that key must be sent to the service in a specific HTTP header. This file describes those providers in one table-like format: what the service is called, what secret keys it needs, which headers they go in, and which API host is allowed.

The important safety idea is the “sentinel.” The sandbox gets an environment variable that looks like a key but is only a placeholder. When the sandbox makes a request to the approved host, the egress proxy replaces that placeholder with the real stored secret on the way out. Like giving someone a valet ticket instead of the car key, the sandbox can use the access path but cannot read or leak the raw secret.

The file currently declares Datadog, including its possible regional API hosts. For services with fixed hosts, the host is simple. For services like Datadog, where the customer’s account belongs to one of several published sites, the user chooses from a closed list. That prevents a key for one region from being sent to a made-up or wrong host.

Finally, `manifest()` packages these declarations into the extension manifest: credential slots plus a prompt section explaining how agents should request and use them.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider declaration is safe and complete as soon as it is created. It prevents a provider from accidentally having both a fixed host and a selectable host list, or neither.

**Data flow**: It reads the provider’s `host`, `sites`, `host_env`, and `site_description` fields after construction. If the declaration is valid, nothing changes and the provider can be used. If the declaration is ambiguous or missing needed host-choice details, it stops immediately by raising an error.

**Call relations**: This is the guardrail for every `KeyedProvider` row in the provider table. Before later code can build credential slots or usage text, this method makes sure the row has one clear way to decide where requests may be sent.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This turns a provider’s host information into the form used by the credential system. It returns either one fixed hostname or a controlled host choice that the workspace member must pick from.

**Data flow**: It reads the provider’s fixed `host` or its `sites` list. If there is no site list, it outputs the fixed host string. If there is a site list, it creates a `HostChoice` containing the slot name, explanation, allowed hosts, default host, and environment variable that will carry the chosen host into the sandbox.

**Call relations**: This is used by `KeyedProvider.slots` when building secure credential injection rules, and by `KeyedProvider.usage` when writing the human-facing example command. It is the shared decision point for “where is this provider allowed to be called?”

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This converts one provider declaration into the credential slots the rest of UFO understands. Each slot says what secret is needed, where it may be injected, and what placeholder the sandbox will use.

**Data flow**: It starts with the provider’s secrets and resolved target host. For each secret, it creates a credential slot with a name, a member-facing description, and an injection rule saying: only for this host, put the stored secret into this HTTP header when the sandbox sends this sentinel value from this environment variable. If the provider also needs a host choice, it adds an extra credential slot for that choice. The result is a tuple of credential slot objects.

**Call relations**: The top-level `manifest` function gathers the slots from every provider by calling this method. These slots are what let the standard credential request flow ask an admin for missing values and let the egress proxy safely swap placeholders for real secrets during outbound requests.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: This writes a short instruction line showing an agent how to call the provider’s API from the sandbox. It names the slots and shows the environment variables that should be used in a `curl` command.

**Data flow**: It reads the provider name, label, secrets, headers, environment variable names, and host information. It builds a plain text sentence listing the required slots and a sample HTTPS request. For providers with selectable hosts, the sample uses the host environment variable instead of a hard-coded hostname.

**Call relations**: The file uses this when building the prompt text shown to agents. It connects the low-level credential declarations to practical guidance: after credentials are filled, this is how the agent should make the API request without asking for secrets in chat.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public package of information for UFO. It returns the manifest that says which credentials exist and what instructions should be added to the agent prompt.

**Data flow**: It reads the global provider table and asks each provider for its credential slots. It also uses the prepared prompt section text. It then creates and returns a `Manifest` containing the extension name, version, all credential slots, and the keyed-provider help section.

**Call relations**: This is the handoff point from this file to the wider system. When UFO loads the extension, it calls `manifest`, and the returned manifest lets the rest of the platform expose credential status, request missing secrets privately, and teach agents how to use keyed providers safely.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

This extension is a bridge between the agent and external MCP servers chosen by a workspace. Instead of baking in a fixed list of tools, it asks a named server what tools it offers, then calls one of those tools when needed. Think of it like visiting a marketplace: first you ask what stalls exist, then you go back to the specific stall with the right order form. The workspace supplies MCP server names, URLs, and optional bearer tokens through a credential slot called `mcp_servers`; this file validates that those URLs are ordinary HTTP or HTTPS addresses and keeps the credential use inside the core process. The file exposes two agent-facing tools. `list_mcp_tools` first returns a compact catalog with names, summaries, and parameter names, because full schemas can be huge. If the agent asks for specific tool names, it returns their full input schemas so the agent can call them correctly. `call_mcp_tool` sends arguments to one server tool and returns structured JSON when available, or joined text otherwise. It treats all server output as untrusted because the external server controls it. It also enforces size limits on requests and responses, failing loudly rather than silently cutting data off.

#### Function details

##### `McpServer._http_url`  (lines 77–80)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validator makes sure a configured MCP server URL starts with `http://` or `https://`. It prevents the extension from accepting unsupported or surprising address formats.

**Data flow**: A URL string comes in while server configuration is being parsed. The function checks it against the allowed pattern; if it matches, the same URL comes out, and if not, validation stops with an error.

**Call relations**: This runs automatically when an `McpServer` object is built from the workspace credential data. That means bad server addresses are rejected before `_server`, `mcp_client`, or any tool call can use them.


##### `mcp_client`  (lines 115–121)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This builds a network client for one MCP server. If the workspace provided an auth token, it prepares an `Authorization: Bearer ...` header so the server can recognize the request.

**Data flow**: An `McpServer` object goes in, containing a URL and maybe an auth token. The function turns that into a FastMCP streamable HTTP client with a timeout, ready to list tools or call a tool.

**Call relations**: After `_server` has found the named server, `_list_mcp_tools` and `_call_mcp_tool` ask this function for the actual client connection. It hands off the protocol details, such as initialization and streaming HTTP framing, to FastMCP.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 124–136)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up one named MCP server from the workspace's `mcp_servers` credential. It makes sure tool calls cannot silently go to an undeclared or misspelled server.

**Data flow**: A tool context and a server name go in. The function reads the extension credentials, parses the JSON configuration into validated server objects, finds the requested name, and returns that server; if anything is missing or unknown, it raises an error.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` start here before doing any network work. It is the gatekeeper between the agent's requested server name and the concrete URL and token used by `mcp_client`.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 139–160)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the agent-facing operation for discovering what a configured MCP server can do. It either returns a compact catalog of all tools or full schemas for a small chosen set of tools.

**Data flow**: The tool context and listing request go in, including the server name and optionally exact tool names. The function resolves the server, asks it for its tool list, then returns either short catalog entries or detailed schema entries wrapped as a tool result; unknown tool names become an error.

**Call relations**: The manifest registers this as the handler for `list_mcp_tools`. Inside the flow, it uses `_server` to find the target, `mcp_client` to speak to it, `_catalog_entry` for browseable summaries, `_schema_entry` for exact call instructions, and `_json_result` or `_bounded_schemas` to package the answer safely.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 163–165)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This reads whether an MCP tool claims it is idempotent, meaning repeated identical calls should not cause extra side effects. That hint helps the caller understand how safe a tool is to retry.

**Data flow**: An MCP tool object goes in. The function checks its optional annotations and returns `true` only when the tool explicitly marks itself as idempotent; otherwise it returns `false`.

**Call relations**: _catalog_entry` and `_schema_entry` both include this safety hint in their output. It is a small shared translator from MCP metadata into the JSON shown to the agent.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 168–184)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one full MCP tool definition into a short catalog item. The goal is to show enough information to choose a tool without flooding the agent with a large JSON schema.

**Data flow**: A full MCP tool object goes in. The function extracts the name, a short summary, parameter names, required parameter names, and the idempotent hint, then returns a compact JSON-friendly dictionary.

**Call relations**: _list_mcp_tools` calls this when the agent is browsing a server without asking for full schemas. It relies on `_summary` to shorten descriptions and `_idempotent` to include the retry-safety hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 187–193)

```
def _summary(description: str) -> str
```

**Purpose**: This creates a short human-readable summary from a longer tool description. It avoids dragging in later documentation sections, such as argument lists, that would make the catalog noisy.

**Data flow**: A description string goes in. The function takes the first line, keeps only the first sentence-like part, trims it to the maximum summary length, and returns that shorter text.

**Call relations**: _catalog_entry` uses this while building the browseable tool catalog. It keeps the first listing small so the agent can later ask for full schemas only for tools it may actually use.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 196–202)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This prepares the detailed view of one MCP tool. It includes the full input schema, which tells the agent the exact parameter names, types, and structure needed for a valid call.

**Data flow**: A full MCP tool object goes in. The function copies out the tool name, description, input schema, and idempotent hint into a JSON-friendly dictionary.

**Call relations**: _list_mcp_tools` calls this only after the agent has named specific tools. It uses `_idempotent` for the safety hint and then hands the larger payload toward `_bounded_schemas` so oversized multi-schema requests can be refused.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 205–217)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the agent-facing operation that invokes one exact tool on one configured MCP server. It protects the system by checking request size before the call and response size before returning data.

**Data flow**: The tool context and call request go in, including server name, tool name, and JSON arguments. The function resolves the server, rejects arguments over the byte limit, sends the call, then returns structured JSON when present or text content otherwise; if the MCP server reports an error, it returns an error tool result.

**Call relations**: The manifest registers this as the handler for `call_mcp_tool`. Its path runs through `_server` and `mcp_client`, uses `_joined_text` when a server answers with text blocks, and uses `_json_result` or `_bounded` to package output without exceeding the response limit.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 220–221)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This collects plain text blocks from an MCP response into one string. It ignores non-text blocks because this extension only returns text or structured JSON to the agent.

**Data flow**: A list of response content blocks goes in. The function keeps the blocks that are MCP text content, joins their text with newlines, and returns the combined string.

**Call relations**: _call_mcp_tool` uses this when a tool call fails or when the server does not provide structured JSON. It turns MCP's block-style response format into the simpler text form used in tool results.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 224–227)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed MCP response size. It fails loudly if text is too large, instead of returning a silently truncated and possibly misleading result.

**Data flow**: A text string goes in. The function measures its encoded byte size; if it fits, the same text comes out, and if it is too large, it raises an MCP-specific error.

**Call relations**: _json_result` uses this for all JSON payloads returned to the agent, and `_call_mcp_tool` uses it for error text from a failed MCP call. It is the shared guardrail around outbound tool-result size.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 230–247)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This decides whether a request for several full tool schemas is small enough to return usefully. If too many schemas would produce an oversized partial preview, it asks the caller to request fewer.

**Data flow**: A JSON payload containing schema entries and the number of requested tools go in. The function estimates the result size; if multiple schemas are too large, it raises a clear error, otherwise it passes the payload on to be returned as JSON.

**Call relations**: _list_mcp_tools` uses this only for full-schema responses. It then hands acceptable payloads to `_json_result`, while refusing oversized multi-tool schema requests so the agent can narrow its request instead of guessing from an incomplete answer.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 250–251)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This wraps a JSON-friendly dictionary as a standard tool result. It is the common exit path for successful listing and calling responses.

**Data flow**: A dictionary goes in. The function serializes it to JSON text, checks that the text is within the size limit through `_bounded`, wraps it in `TextContent`, and returns a `ToolResult`.

**Call relations**: _list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` all use this when they need to send structured data back to the agent. It centralizes the final formatting and size check.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 254–285)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the host system: its name, version, tools, input models, handlers, and required credential slot. Without it, the host would not know that `list_mcp_tools` and `call_mcp_tool` exist.

**Data flow**: No runtime request data goes in. The function builds and returns a manifest object that describes two untrusted tools and one credential slot named `mcp_servers`.

**Call relations**: The extension loader calls this during setup to learn what this file offers. The manifest connects the public tool names to `_list_mcp_tools` and `_call_mcp_tool`, and tells the host where the workspace's MCP server configuration must be supplied.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Evaluation connectors
Deterministic fake mailbox, calendar, and code-search connectors provide test-only backends through the same connector path as production tools.

### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `extension registration and eval tool calls`

This file gives evaluations a controlled little world to work in. Instead of calling a real email service, calendar, or code search system, the agent talks to eval-only providers that behave like real connectors from the agent’s point of view. That matters because it tests the production connector path, not a shortcut mock.

The email and calendar tools store their data in workspace-scoped database tables. “Workspace-scoped” means each test workspace sees only its own messages and events, like separate notebooks for separate experiments. Sending an email inserts a sent-message row. Creating, updating, or cancelling an event changes calendar rows. Listing tools read those same rows back, so a grader can check exactly what the agent changed.

The code-search tool is different. It is read-only. Tests seed a complete response under a search query, and the broker returns those exact bytes. This lets evaluations control large or carefully shaped search responses without expecting an agent to pass huge JSON as tool arguments.

The file also declares the tool catalog, argument schemas, a small fake OAuth provider, and the `manifest()` function that registers the three eval connectors. Without this file, evaluations would lose their realistic but repeatable email, calendar, and code-search environment.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Opens a database transaction for this eval extension’s private storage. Other functions use it when they need to read or change the fake mailbox or calendar safely.

**Data flow**: It takes no direct input. It builds an extension context using the eval environment name and no declared credentials, then returns a transaction object. Callers enter that transaction, run database statements, and leave with the changes committed or rolled back by the surrounding context.

**Call relations**: The email and calendar helpers call this whenever they touch stored rows. It is the shared doorway into the durable test data used by `_send_email`, `_list_emails`, `_create_event`, `_list_events`, and `_change_event`.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a Python datetime value. If the string has no time zone, it treats it as UTC so stored calendar times are consistent.

**Data flow**: It receives a text timestamp. It parses the text into a datetime object, adds the UTC time zone when none was provided, and returns the normalized datetime.

**Call relations**: Calendar creation and event updates call this before saving start or end times. It keeps `_create_event` and `_update_event` from storing ambiguous time values.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one eval provider, optionally filtered by a search phrase. This is how the connector can answer, “What can this provider do?”

**Data flow**: It receives a workspace id, provider name, and query string. It looks up the provider’s catalog, compares the query to tool slugs and descriptions, and returns either matching tools or the full catalog when nothing specific matches.

**Call relations**: `EvalEnvBroker.search` calls this when a tool search is requested. It sits near the start of the connector flow, before any specific tool is described or executed.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the definition for one named tool, including its input shape. This lets the connector tell the agent exactly what arguments a tool accepts.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans that provider’s catalog and returns the matching `BrokerTool`; if none exists, it raises an unknown-tool error.

**Call relations**: This supports the describe-tool part of the connector path. If an agent or caller asks for a tool that is not in the eval catalog, it stops the request with `UnknownBrokerTool`.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Dispatches an actual tool call to the right fake email, calendar, or code-search operation. It is the main switchboard for eval connector calls.

**Data flow**: It receives the workspace id, provider name, tool slug, raw arguments, account id, and optional idempotency key. It validates the raw arguments using the right argument model, calls the matching helper, and returns that helper’s result. If the provider or slug is not recognized, it raises an unknown-tool error.

**Call relations**: This is called when the connector system asks the broker to run a tool. It hands off to `_send_email`, `_list_emails`, `_create_event`, `_list_events`, `_update_event`, `_cancel_event`, or `_search_code` depending on the requested provider and slug.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns a pre-seeded code-search response for an exact query. It deliberately fails if the fixture is missing, so a broken test setup is caught loudly.

**Data flow**: It receives validated search arguments containing a query. It reads the scoped store entry named with the code-search prefix plus that query. If the stored value is a dictionary, it returns a copy of it; otherwise it raises an error.

**Call relations**: `execute` calls this for the `search_code` tool. Unlike email and calendar helpers, it does not change database tables; it reads the response that the evaluation author seeded earlier.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Adds a sent email to the fake mailbox. This lets an agent’s “send email” action leave a durable record that the evaluation can later grade.

**Data flow**: It receives a workspace id and validated email arguments. It creates a new email id, opens a transaction, inserts a row in the sent folder with the fixed assistant sender address, recipients, subject, body, and current UTC time, then returns the new id and sent status.

**Call relations**: `execute` calls this when the requested email tool is `send_email`. It uses `_transaction` to write to the eval email table that `_list_emails` and graders can later read.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads messages from the fake mailbox, newest first, with optional text filtering. This lets the agent inspect seeded or previously sent email.

**Data flow**: It receives a workspace id and validated list arguments. It builds database conditions for the workspace and folder, adds a sender/subject/body substring filter when a query is provided, reads up to the requested limit, and returns email dictionaries with ids, addresses, subject, body, and send time.

**Call relations**: `execute` calls this for the `list_emails` tool. It uses `_transaction` for the database read and returns rows created by seeding or by `_send_email`.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a confirmed event in the fake calendar. This gives evaluations a durable record of calendar actions taken by the agent.

**Data flow**: It receives a workspace id and validated event arguments. It creates a new event id, parses the start and end strings into datetimes, inserts a confirmed event row with attendees, and returns the new id and confirmed status.

**Call relations**: `execute` calls this when the calendar tool is `create_event`. It relies on `_moment` to normalize time strings and `_transaction` to save the row used later by list, update, cancel, or grading code.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Reads calendar events for a workspace in start-time order, optionally filtered by title. Cancelled events are still listed with their status.

**Data flow**: It receives a workspace id and validated list arguments. It builds a workspace condition, adds a title substring filter if requested, selects matching rows up to the limit, converts each row to a plain response dictionary, and returns them under `events`.

**Call relations**: `execute` calls this for the `list_events` tool. It uses `_transaction` to read stored events and `_event_json` to shape each database row into the API response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares requested changes for an existing calendar event. It only changes fields that the caller actually supplied.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary from non-empty title, start, end, and attendees fields, parsing any new times. If there is nothing to change, it raises an error; otherwise it passes the changes onward and returns the updated event.

**Call relations**: `execute` calls this for the `update_event` tool. It is the careful front end to `_change_event`, translating user-facing arguments into database column changes.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled. It does not delete the event, so the final state can show that the event was cancelled.

**Data flow**: It receives a workspace id and validated cancel arguments. It builds a one-field change setting the status to cancelled, sends that to the shared event-changing helper, and returns the updated event response.

**Call relations**: `execute` calls this for the `cancel_event` tool. It reuses `_change_event`, the same shared path used by event updates.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the updated event. It also protects workspace boundaries by only changing an event in the caller’s workspace.

**Data flow**: It receives a workspace id, event id string, and a dictionary of database changes. It opens a transaction, updates the matching event row, checks that exactly one row changed, fetches the updated row, converts it to response form, and returns it. If no event matches, it raises an error.

**Call relations**: `_update_event` and `_cancel_event` both call this after deciding what should change. It uses `_transaction` for the database work and `_event_json` to produce the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a calendar database row into the plain dictionary returned by calendar tools. It keeps event responses consistent across listing, updating, and cancelling.

**Data flow**: It receives a database row. It extracts the id, title, start and end times, attendees, and status, converts ids and times to strings, and returns a JSON-friendly dictionary.

**Call relations**: `_list_events` uses this for each listed event, and `_change_event` uses it after an update or cancellation. It is the final formatting step for calendar responses.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Declares that eval environment tools do not produce downloadable files. This satisfies the broker interface while making the behavior explicit.

**Data flow**: It receives a tool response but does not inspect it. It always returns an empty tuple, meaning there are no file attachments or file outputs to collect.

**Call relations**: The connector framework may ask brokers for files after a tool call. For these eval providers, this method ends that path immediately because email, calendar, and code search responses are plain data.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for eval environment providers. These fake tools only accept structured arguments, not uploaded files.

**Data flow**: It receives upload details such as workspace, provider, tool slug, filename, MIME type, and checksum. It ignores them and raises an error saying uploads are not accepted.

**Call relations**: If the connector framework ever tries to prepare a file upload for these providers, this method stops it. No other helper receives the upload because uploads are outside this eval environment’s design.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool lookup results in the connector’s search response format. It supports the flow where a caller searches for relevant external tools.

**Data flow**: It receives a workspace id, provider name, and search query. It asks `tools` for matching tools, puts them into a `BrokerSearch` object, and returns that object.

**Call relations**: This is the search-facing entry into the broker’s tool catalog. It delegates the actual matching to `tools` and packages the answer in the shape expected by the connector system.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple fake bearer credential for the eval provider account. It lets the connector path proceed without needing a real external login token.

**Data flow**: It receives a workspace id, provider name, and account id. It creates and returns a credential whose bearer token is a predictable eval-only string containing the account id.

**Call relations**: The connector framework can call this when it needs credentials for a provider. Since these providers are local eval fakes, the returned token is only a placeholder for the normal authentication step.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a fake OAuth authorization URL. OAuth is the common web sign-in flow where a user grants an app access, but evals normally seed grants directly instead.

**Data flow**: It receives a state value and redirect URI. It formats them into an HTTPS URL using the provider’s fake host and returns that string.

**Call relations**: This exists because connector providers need an OAuth descriptor. It would be used if someone started the connect flow, but normal evaluations do not rely on it.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange by returning the fixed eval account id. It keeps the provider honest enough to satisfy the connector interface without contacting a real service.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It does not validate them against an external service; it returns an `OAuthAccount` with the eval account id.

**Call relations**: This is the second half of the fake connect flow after `authorize_url`. If driven, it hands the connector system the account identity expected by the eval environment.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Registers the eval email, calendar, and code-search providers with the extension system. This is the file’s public entry point for discovering the eval connectors.

**Data flow**: It creates one shared `EvalEnvBroker`, then builds a `Manifest` containing three connector providers. Each provider has a fake OAuth descriptor, a human-readable label, and the shared broker that knows how to run its tools.

**Call relations**: The extension loader calls this to learn what the extension offers. It wires `_EvalEnvOAuth` and `EvalEnvBroker` into `ConnectorProvider` objects so later connector discovery, description, and execution can reach the broker methods above.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).

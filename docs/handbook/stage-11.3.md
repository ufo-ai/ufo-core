# External connector, research, and business-tool execution  `stage-11.3`

This stage is the system’s “outside world” workbench. It is used during the main work loop when the assistant must search the web, call a business app, or use a connected account. The key idea is safety: outside services are reached through brokers and adapters, so tools can work without exposing raw passwords or tokens.

The generic connector tools are the front desk. They let the agent discover available tools, run them, and pass input or output files between the workspace and connector brokers. Composio support provides account consent, tool discovery, tool execution, file upload handling, MCP-style calls, and proxy HTTP calls where Composio holds the real provider secret. Pipedream support does a similar job for Pipedream actions. MCP support lets the agent list and call tools from workspace-configured MCP servers, which are services that publish callable tools.

Research tools route web search and page fetching through Exa or another selected provider. Slack tools help connect a workspace, create an app manifest, and search messages. YC tools safely talk to Y Combinator’s command-line system. The evaluation environment supplies fake email and calendar connectors for tests, so the same paths can run without contacting real services.

## Files in this stage

### Composio brokerage
Composio integration files provide the safe connector broker, API client, MCP tool call transport, and provider-token proxying layer.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

Think of this file as a front desk for Composio-backed connectors. The rest of the project asks for simple things like “show me the Gmail tools,” “run this tool,” or “give me safe access to this account.” This broker translates those requests into Composio API calls and turns Composio’s answers back into UFO’s own connector objects.

The broker is deliberately stateless. It asks for the current Composio client each time instead of storing one. That matters because tests or deployments may swap the network transport, and this design avoids stale connections or hidden shared state.

The main class, `ComposioBroker`, covers the connector lifecycle. It can list tools, fetch a tool’s input schema, execute a tool for a workspace’s broker user, search with Composio’s tool router, prepare file uploads, and create a safe credential transport. File handling is important: Composio may return downloadable file links inside nested response data, and this broker finds them. For uploads, it asks Composio for a temporary upload slot and returns the exact argument a tool should receive.

The file also protects against confusing or unsafe failures. If an account no longer belongs to this broker user, it tells the agent to ask the member to reconnect instead of pretending the problem is a missing tool. If a tool slug is wrong, it tries to enrich the error with real available slugs so the next attempt can be better.

#### Function details

##### `ComposioBroker.tools`  (lines 48–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds tools for a provider, such as a connected app or service, using a search query. It returns them in UFO’s standard `BrokerTool` shape so the rest of the system does not need to understand Composio’s raw catalog format.

**Data flow**: It receives a workspace id, a provider name, and a search query. It turns the provider into Composio’s toolkit name, asks the current Composio client for matching tools, then converts the raw listing into clean tool records with slugs and short descriptions. The result is a tuple of broker tools.

**Call relations**: When another part of the connector system needs to discover what actions are available, it calls this method. This method gets the active Composio client, uses `_toolkit` to choose the right Composio toolkit name, and hands the API response to `_discovered_tools` to make it usable by UFO.

*Call graph*: calls 2 internal fn (_discovered_tools, _toolkit); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–65)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the input instructions for one specific Composio tool. This tells the system what arguments the tool expects, and rewrites file-upload inputs into the project’s own workspace-file vocabulary.

**Data flow**: It receives a workspace id, provider name, and tool slug. It asks Composio for that tool’s schema, extracts the description and input schema, rewrites file upload fields through `workspace_file_schema`, and returns a `BrokerTool`. If Composio says the slug does not exist, it raises `UnknownBrokerTool` so callers know the requested tool is not valid.

**Call relations**: This is used after a tool has been chosen and the system needs exact input requirements. It talks directly to the Composio client, then packages the answer as a `BrokerTool` for the rest of UFO. It only translates a 404-not-found response into `UnknownBrokerTool`; other Composio errors are left for higher layers to handle.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 67–90)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for a workspace’s broker user and a specific connected account. It also turns common failure cases into more helpful errors, such as telling the user to reconnect a stale account.

**Data flow**: It receives the workspace id, provider, tool slug, tool arguments, connected account id, and an optional idempotency key, which helps avoid duplicate effects when retrying. It builds the Composio external user id from the workspace id and sends the execution request. On success, it returns Composio’s response dictionary. On failure, it may replace the error with a reconnect message or with a better “unknown slug” message that includes available tool slugs.

**Call relations**: This is the main path when an agent actually performs an action through Composio. It calls the current Composio client to run the tool. If Composio reports a possible stale connected account, it asks `_stale_account` and `_reconnect_error` to produce the right guidance. If the slug is missing, it calls `_slug_miss` to try to add useful discovery information before passing the error upward.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 92–97)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a tool execution response. This matters because Composio can hide file objects anywhere inside nested response data, not just at the top level.

**Data flow**: It receives a response dictionary from a tool execution. It creates an empty list, asks `_collect_files` to walk through the whole response, and returns every discovered file as `BrokerFile` objects. The response itself is not changed.

**Call relations**: After `execute` or another tool-running path receives a Composio response, callers can use this method to extract downloadable outputs. It delegates the recursive searching to `_collect_files`, keeping this public method small and focused.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 99–115)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a temporary upload location for a file that will be passed into a Composio tool. It gives the caller both the upload URL and the argument value the tool should later receive.

**Data flow**: It receives workspace id, provider, tool slug, filename, MIME type, and MD5 checksum. It maps the provider to a Composio toolkit, asks Composio to create an upload slot, and returns a `StagedUpload` containing the PUT URL, content type, and a tool argument with the file name, MIME type, and Composio storage key.

**Call relations**: This is used before executing a tool that needs a file input. It calls `_toolkit` to speak Composio’s provider naming language and uses the active Composio client to reserve storage. The returned `StagedUpload` is then used by upload code and by the later tool call.

*Call graph*: calls 1 internal fn (_toolkit); 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 117–120)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for useful connector tools through Composio’s tool router, which is a higher-level search service for matching a user query to tools.

**Data flow**: It receives a workspace id, provider, and query. It gets the current Composio client and passes all of that to `search_connector_tools`. The result is a `BrokerSearch` object that represents the search answer.

**Call relations**: This supports discovery flows where the system is not simply listing catalog entries but asking Composio to route a query to relevant tools. It is a thin handoff to the Composio client helper, keeping the broker as the shared entry point for connector search.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 122–138)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe credential object for a connected account, without exposing the actual provider token. It first checks that the account belongs to this workspace’s broker user, then returns a transport that sends provider HTTP requests through Composio’s proxy.

**Data flow**: It receives a workspace id, provider, and connected account id. It builds the expected broker-user id, asks Composio to confirm that this account belongs to that broker user and toolkit, and then constructs a `Credential` whose transport is a `ComposioProxyTransport`. If the account is not found for this broker user, it raises a reconnect-style error instead of returning access.

**Call relations**: This is used when code needs to talk to a provider through an authenticated account. It calls `_toolkit` to identify the provider correctly and `_reconnect_error` if ownership validation fails. On success it wraps Composio’s API base, API key, account id, and HTTP transport into `ComposioProxyTransport`, then hands that to `Credential`.

*Call graph*: calls 2 internal fn (_reconnect_error, _toolkit); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 140–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by trying to include real tool slugs from the same Composio toolkit. This helps the agent recover from a bad slug instead of guessing blindly.

**Data flow**: It receives the Composio client, provider, missing slug, and original Composio error. It turns the slug into search words, asks Composio for nearby tools, and if needed falls back to listing tools with an empty query. If it finds tools, it returns a new `ComposioError` whose message includes available slugs. If discovery fails or finds nothing, it returns the original error unchanged.

**Call relations**: This helper is called by `ComposioBroker.execute` only when Composio reports a not-found error while running a tool. It uses `_toolkit` to choose the catalog, `_discovered_tools` to normalize search results, and `ComposioClient.list_tools` to gather suggestions.

*Call graph*: calls 3 internal fn (_discovered_tools, _toolkit, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through nested response data and collects any Composio file objects it finds. A file object is recognized by the presence of a file URL, MIME type, and name.

**Data flow**: It receives any value and a list that is being filled. If the value looks like a Composio file object, it appends a `BrokerFile` with the file name and URL. If the value is a dictionary or list, it recursively checks each contained item. It returns nothing, but the `found` list is changed.

**Call relations**: `ComposioBroker.file_outputs` starts this search after a tool response is available. `_collect_files` does the deep walk, like checking every drawer inside a cabinet, and adds each discovered file to the shared list.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error is really saying that the connected account is gone or no longer known. This prevents the system from treating a dead account as if the tool slug were wrong.

**Data flow**: It receives a Composio error and the connected account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account: Composio’s own phrase “connected account” with “not found,” or the exact account id with “not found.” It returns `true` if the error matches that stale-account pattern, otherwise `false`.

**Call relations**: `ComposioBroker.execute` calls this when execution fails. If it returns true, execution switches to `_reconnect_error` so the user is told to reconnect the account. If it returns false, execution continues with the normal error handling path, including possible slug-miss help.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a Composio error message that tells the user to reconnect the provider account. It keeps the original error status and body, then adds project-standard stale-grant guidance.

**Data flow**: It receives the original Composio error and provider name. It asks `stale_grant_guidance` for the right human-facing reconnect instruction, appends that to the original error body, and returns a new `ComposioError` with the same status code.

**Call relations**: Both `ComposioBroker.execute` and `ComposioBroker.credential` use this when they determine the connected account is no longer usable. It centralizes the wording so stale-account failures lead to the same next step: reconnect the provider.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_toolkit`  (lines 195–197)

```
def _toolkit(provider: str) -> str
```

**Purpose**: Translates UFO’s provider name into Composio’s toolkit slug. If the provider is not in the connector registry, it falls back to using the provider string as-is.

**Data flow**: It receives a provider name. It looks that name up in `composio.CONNECTORS`; if a connector specification exists, it returns that specification’s toolkit value. Otherwise it returns the original provider name.

**Call relations**: Several broker methods call this before speaking to Composio, because Composio may use a different name for the same provider. It supports tool listing, upload staging, credential validation, and slug-miss discovery.

*Call graph*: called by 4 (_slug_miss, credential, stage_upload, tools).


##### `_discovered_tools`  (lines 200–222)

```
def _discovered_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio’s raw tool-list response into simple `BrokerTool` objects. It keeps only usable tool names and short descriptions.

**Data flow**: It receives a dictionary from Composio’s list-tools API. It looks for an `items` list, skips malformed entries, takes each tool’s `slug` or `name`, trims long descriptions to a fixed cap, and returns a tuple of `BrokerTool` objects. If the response does not contain a usable list, it returns an empty tuple.

**Call relations**: `ComposioBroker.tools` uses this for normal tool discovery, and `ComposioBroker._slug_miss` uses it when building helpful alternatives after a missing-slug error. It is the small adapter that turns Composio’s catalog shape into UFO’s connector shape.

*Call graph*: called by 2 (_slug_miss, tools); 1 external calls (__init__).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector OAuth, tool discovery, tool execution, and file upload preparation`

This file is the bridge between UFO and Composio’s web API. Composio acts like a concierge for many third-party services: instead of UFO learning every provider’s login and tool system itself, it asks Composio to create OAuth consent links, remember which account was connected, list tools, and execute tools on the user’s behalf. OAuth is the common “sign in and allow access” flow used by services like Google or Slack.

The file starts with a registry of supported connectors. Each entry says the friendly name, Composio toolkit name, provider host, and sometimes the command-line environment variable a tool expects. The main class, ComposioClient, sends HTTP requests to Composio using an API key from the environment. It checks responses carefully and raises ComposioError if Composio fails or returns data in the wrong shape.

A key safety idea is that UFO stores only a connected account id, not the real access token. When a tool runs, Composio injects the token on its own server side. The file also supports semantic tool search through Composio’s Tool Router, with cached sessions so repeated searches do not reopen the same session over and over. Finally, it rewrites file-upload tool schemas so the agent sees a simple workspace file path rather than Composio’s internal storage details.

#### Function details

##### `ComposioError.__init__`  (lines 122–125)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error when Composio returns a failed response or data this client cannot safely use. It keeps the HTTP status and response body so callers can see what went wrong.

**Data flow**: It receives a status code and response text → builds a message like “composio 403: ...” → stores the status and body on the exception for later inspection.

**Call relations**: This is used throughout the client whenever a Composio call is unusable. Higher-level methods such as connect_link, connected_account, create_upload, tool_router_session, _auth_config, and _body raise it instead of silently pretending the operation worked.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 142–151)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to approve access to a connector, such as Slack or Google Calendar. Without this, the user could not start the consent flow for a new connected account.

**Data flow**: It receives a toolkit name, broker user id, and callback URL → finds or creates the right Composio auth configuration → posts a link request to Composio → returns the redirect URL the user should visit. If the response has no valid URL, it raises ComposioError.

**Call relations**: This is the front door for OAuth setup. It first calls _auth_config to choose the login configuration, then calls _post to ask Composio for the hosted consent link.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 153–177)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Checks that a connected account id is real, active, owned by the expected workspace user, and for the expected connector. This prevents one user or connector from accidentally or maliciously using another account’s grant.

**Data flow**: It receives an account id, expected user id, and expected toolkit → fetches the account record from Composio → checks ownership, active status, and toolkit slug → returns an OAuthAccount containing the approved account id. If any check fails, it raises ComposioError.

**Call relations**: This function is used after the consent flow or during grant validation. It relies on _get to read Composio’s account metadata and returns the safe local representation, OAuthAccount, only after the checks pass.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.list_tools`  (lines 179–185)

```
async def list_tools(self, toolkit: str, query: str='', limit: int=TOOL_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Asks Composio for tools available in a given toolkit, optionally narrowed by a search query. This is useful when the system needs to discover tool slugs rather than relying on a fixed hard-coded list.

**Data flow**: It receives a toolkit name, optional query, and limit → turns them into URL query parameters → sends a GET request to Composio’s tools endpoint → returns Composio’s response as a dictionary.

**Call relations**: This is called by the broker when it misses a known tool slug and needs to search Composio’s catalog. It delegates the actual HTTP work to _get.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 187–188)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed schema for one Composio tool. A schema describes what inputs the tool accepts, like a form explaining which fields are required.

**Data flow**: It receives a tool slug → requests that tool’s detail endpoint → returns the response dictionary from Composio.

**Call relations**: This is a small catalog lookup helper. It uses _get, so response parsing and error handling are shared with the rest of the client.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.execute_tool`  (lines 190–204)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on behalf of a broker user, optionally tied to a specific connected account. This is where discovered connector actions actually happen.

**Data flow**: It receives a tool slug, arguments, user id, optional connected account id, and optional idempotency key → builds the execute request body → refuses requests larger than the configured payload limit → posts to Composio’s execute endpoint → returns the result dictionary. An idempotency key, when supplied, tells the server to treat retries as the same operation rather than duplicate work.

**Call relations**: This is the execution path after a tool has been chosen. It hands the HTTP request to _post; Composio then runs the tool server-side and injects the provider token itself.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 206–230)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a temporary place to stage a file before a tool uses it. This lets the sandbox upload bytes directly to Composio’s storage while the tool later receives only a storage key.

**Data flow**: It receives toolkit, tool slug, filename, MIME type, and MD5 checksum → posts an upload request to Composio → checks that Composio returned a storage key → returns a ComposioUpload with that key and, if needed, a presigned PUT URL. If Composio says the file already exists, the URL is absent and the existing key is reused.

**Call relations**: This function supports connector tools that accept files. It calls _post for the upload-request API and creates a ComposioUpload object after validating the response shape.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 232–243)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The Tool Router is a search service that can suggest relevant tools and advice for a user’s goal.

**Data flow**: It receives a user id and list of toolkits → posts a session request to Composio → reads the session id and MCP URL from the response → returns a ToolRouterSession. MCP means Model Context Protocol, a standard way for tools to be exposed to an AI system.

**Call relations**: search_connector_tools calls this when no cached session exists for the user and toolkit. Once created, the returned session URL is used to call the search tool through mcp_session.mcp_call_tool.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 245–263)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the Composio authentication configuration for a toolkit, or creates a managed one if none exists. This chooses what OAuth setup the user consent link will use.

**Data flow**: It receives a toolkit slug → asks Composio for an existing auth config → extracts the first config id if present → otherwise posts a request to create a managed auth config → returns the config id. If the created response lacks an id, it raises ComposioError.

**Call relations**: connect_link calls this before creating a consent URL. It uses _get, _post, and _auth_config_id to keep the link creation path independent of whether the config already existed.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 265–267)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a GET request to Composio and returns a checked response body. It centralizes the common pattern of opening an HTTP client, making a read request, and parsing the result.

**Data flow**: It receives a path and optional query parameters → opens an HTTP client configured for Composio → performs the GET request → passes the response to _body → returns the parsed dictionary.

**Call relations**: Higher-level read methods use this helper: _auth_config, connected_account, list_tools, and tool_schema. It gets the configured HTTP client from _http and leaves response validation to _body.

*Call graph*: calls 2 internal fn (_http, _body); called by 4 (_auth_config, connected_account, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 269–273)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a POST request to Composio and returns a checked response body. It is the shared helper for operations that create links, sessions, uploads, auth configs, or executions.

**Data flow**: It receives a path, JSON body, and optional headers → opens an HTTP client configured for Composio → posts the JSON to the path → passes the response to _body → returns the parsed dictionary.

**Call relations**: Most write-style client methods call this, including connect_link, execute_tool, create_upload, tool_router_session, and _auth_config. Like _get, it uses _http for setup and _body for validation.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 275–281)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Composio. It applies the base API URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the client’s api_key and optional transport → creates an httpx asynchronous client with Composio defaults → returns that client to be used in an async context.

**Call relations**: _get and _post call this for every request. Creating a fresh client each time helps avoid leaked connections and lets tests replace the transport cleanly.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 284–292)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a usable dictionary, or raises a clear error if the response is bad. This keeps callers from accidentally trusting failed or strangely shaped responses.

**Data flow**: It receives an HTTP response → raises ComposioError for status codes 400 and above → returns an empty dictionary for an empty body → parses JSON → confirms the JSON is an object/dictionary → returns it. Non-object JSON is treated as an error.

**Call relations**: _get and _post both send their responses here. Because every Composio request passes through this function, the client has one consistent place for response checking.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 295–321)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio tool input schemas so file inputs are shown to the agent as simple workspace file paths. This hides Composio’s internal upload storage format from the model.

**Data flow**: It receives any schema-like value → walks through dictionaries and lists recursively → when it finds a field marked as file_uploadable, replaces it with an object containing a workspace_file string path → returns the rewritten value. Other values are left unchanged.

**Call relations**: _search_result calls this when building BrokerTool entries from Tool Router search results. It makes searched tools easier and safer for the agent to call.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 324–331)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small safety helper for reading only the shape this client expects.

**Data flow**: It receives a response dictionary → looks for an items list → scans for the first item with a string id → returns that id, or None if none is found.

**Call relations**: _auth_config uses this after asking Composio for existing auth configs. If it returns None, _auth_config knows it must create a new managed config.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 334–341)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the deploy’s default ComposioClient using the COMPOSIO_API_KEY environment variable. It fails loudly if the key is missing because connector OAuth cannot work without it.

**Data flow**: It reads COMPOSIO_API_KEY from the process environment → if missing, raises RuntimeError → if present, creates and returns a ComposioClient using that key.

**Call relations**: Other parts of the extension can call this when they need the standard Composio client for the running deployment. It is the boundary between environment configuration and API use.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 348–349)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This avoids crashes or mistaken assumptions when Composio returns optional or loosely shaped data.

**Data flow**: It receives any value → returns the value unchanged if it is a dictionary → otherwise returns an empty dictionary.

**Call relations**: _search_result uses this repeatedly while reading nested Tool Router responses. It keeps the parsing code defensive and simple.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 352–355)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. This filters out missing, empty, or non-string values from Composio responses.

**Data flow**: It receives any value → if the value is not a list, returns an empty tuple → otherwise keeps only items that are non-empty strings → returns them as a tuple.

**Call relations**: _search_result uses this to read tool slugs, plan steps, guidance, and pitfalls from search results without trusting every field blindly.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 358–392)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts a raw Tool Router search response into the project’s BrokerSearch format. That format contains the matched tools plus practical advice like plan steps, guidance, and pitfalls.

**Data flow**: It receives a response dictionary → finds the result data and tool schemas → gathers primary and related tool slugs without duplicates → builds BrokerTool objects with descriptions and rewritten input schemas → collects recommended plan steps, guidance, and known pitfalls → returns a BrokerSearch object.

**Call relations**: search_connector_tools calls this after the MCP search tool returns. It uses _dict, _str_tuple, and workspace_file_schema to turn Composio’s flexible response into stable data the broker can render.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 395–420)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for useful tools for a connector based on a plain-language query. It uses Composio’s Tool Router so the agent can discover tools semantically, not only by exact slug names.

**Data flow**: It receives a ComposioClient, workspace id, connector name, and search query → maps the connector to its Composio toolkit → builds a broker user id from the workspace id → reuses or creates a cached Tool Router session under a lock → calls the COMPOSIO_SEARCH_TOOLS MCP tool with the query → converts the raw result with _search_result → returns BrokerSearch.

**Call relations**: This is the high-level search path for dynamic connector tools. It may call ComposioClient.tool_router_session to create a session, then calls mcp_session.mcp_call_tool to perform the search, and finally hands the response to _search_result for shaping.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio’s Tool Router. The Tool Router is reached through MCP, which stands for Model Context Protocol: a standard way for AI systems to discover and call tools. In this case, the project only uses the router for searching tools, not for executing them.

The main job here is to open a temporary HTTP-based MCP session, call one named tool with some arguments, then close the session. Think of it like walking up to an information desk, asking one question, writing down the answer in a simple format, and leaving.

The file also smooths over the different shapes a response might have. If the MCP library already provides parsed dictionary data, it returns that. If not, it checks another structured field. If that still is not available, it looks for a text response that contains JSON and parses it. If the text is not JSON, it returns the text in a dictionary. This fallback chain matters because different tools or servers may package their answers slightly differently, but callers of this function should still get a predictable plain dictionary back.

A useful testing detail is that the MCP client is imported as a module-level name, so tests can replace it with a fake client instead of making real network calls.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one tool on a remote MCP endpoint and returns the answer as a plain dictionary. It is used when the project needs to ask Composio’s Tool Router something, especially for tool search, without exposing the rest of the code to MCP session details.

**Data flow**: It receives the endpoint URL, the tool name, the tool arguments, HTTP headers, and a timeout. It opens a streamable HTTP connection using those details, sends the tool call, waits for the result, and then closes the connection. It first tries to return already-parsed dictionary data, then structured dictionary content, then JSON found inside a text response; if none of those fit cleanly, it wraps the remaining value in a simple dictionary.

**Call relations**: This function is the boundary between local code and the external MCP service. To do its work, it builds a StreamableHttpTransport for the endpoint, uses fastmcp.Client as the session wrapper, and uses json.loads only when it has to turn a text response into normal data. After this function returns, callers can work with a regular dictionary instead of knowing how the MCP client represented the response.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Some connectors need to talk to services like calendars, CRMs, or other APIs, but Composio keeps the real provider credential hidden on its own servers. This file is the bridge that makes that still feel like an ordinary HTTP call. Think of it like sending a sealed letter through a trusted courier: the connector writes where it wants to go and what it wants to ask, Composio adds the private credential, and the provider’s answer comes back through the courier.

The main piece, `ComposioProxyTransport`, plugs into `httpx`, an HTTP client library. When code tries to call the provider, this transport reads the original request, copies the method, URL, query parameters, selected headers, and body, and packages them into a Composio `POST /tools/execute/proxy` request. It deliberately removes unsafe or transport-specific headers such as authorization, host, and content length, because Composio and the HTTP layer must supply those correctly.

When Composio replies, the transport either returns the error as-is or rebuilds the provider’s original status code, headers, and body so pagination and other header-based behavior still work.

The second piece, `ComposioRequestForwarder`, is used by a broker-style forwarding path. It performs one proxied provider request with size and time limits, so a slow or huge response cannot trap the shared proxy process.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 59–98)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request translator. It takes a normal provider HTTP request, rewrites it as a Composio proxy-execute request, sends it, and returns a normal-looking HTTP response to the caller.

**Data flow**: It receives an `httpx.Request` aimed at the provider. It reads the request body, builds a JSON payload containing the connected account id, target endpoint, HTTP method, query parameters, safe headers, and body, then sends that payload to Composio using the inner transport. It reads Composio’s response with the configured size rule; if Composio reports an error, it returns that error response, otherwise it reconstructs the provider response and returns it.

**Call relations**: This function is the center of `ComposioProxyTransport`. It calls `_read_bounded` so broker replies do not grow without limit, and it calls `_provider_response` when the Composio call succeeded so the rest of the connector can keep behaving as if it had spoken directly to the provider.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 100–115)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response body from Composio, optionally enforcing a maximum size. The limit protects shared proxy processes from buffering a response that is too large.

**Data flow**: It receives an HTTP response from Composio. If no maximum size is set, it simply reads the whole body. If a maximum is set, it reads the body chunk by chunk, counts the bytes, closes the response early if the limit is exceeded, and raises a Composio error instead of returning an oversized body.

**Call relations**: `handle_async_request` calls this immediately after Composio replies. Its job is to make the next step safe: either there is a complete response body ready to inspect, or the request fails loudly because the broker response was too large.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 117–139)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio’s proxy-execute JSON result back into an ordinary HTTP response from the provider. It exists so callers can read status codes, headers, and bodies the same way they would after a direct provider call.

**Data flow**: It receives the decoded Composio payload and the original provider request. It unwraps nested `data` envelopes if Composio has wrapped the provider result more than once, chooses the provider status code, copies usable headers while dropping body-specific transport headers, serializes the provider data into bytes, and returns a new `httpx.Response` attached to the original request.

**Call relations**: `handle_async_request` calls this only after a successful Composio proxy call. It is the final conversion step that hides the fact that Composio was in the middle.

*Call graph*: called by 1 (handle_async_request); 3 external calls (Response, dumps, cast).


##### `ComposioProxyTransport.aclose`  (lines 141–142)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport used to contact Composio. It is needed to release network resources cleanly.

**Data flow**: It takes no new input beyond the transport object itself. It forwards the close request to the inner transport, which can then close open connections or other resources.

**Call relations**: Code that creates a `ComposioProxyTransport`, such as `ComposioRequestForwarder.forward`, calls this during cleanup. It keeps the proxy path from leaking connection resources after a request is finished.


##### `ComposioRequestForwarder.forward`  (lines 159–187)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This performs one provider request through Composio for the broker forwarding path. It adds practical safety limits: a maximum response size and a wall-clock timeout for the whole exchange.

**Data flow**: It receives an account id, HTTP method, URL, headers, and raw body bytes. It gets the Composio client and API key, builds a `ComposioProxyTransport` for the target connected account, creates an HTTP request with a timeout, sends it through the transport, reads the response body, and returns a `ForwardedResponse` containing the status, headers, and body. If the whole operation takes too long, it raises a Composio timeout error, and it always closes the transport afterward.

**Call relations**: This is the entry point for the broker-style forwarding use case in this file. Instead of callers directly using an `httpx` client, they call this forwarder, which creates `ComposioProxyTransport`, relies on its `handle_async_request` method to do the proxy rewrite, and then packages the result into the project’s `ForwardedResponse` type.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### Connector facade and eval doubles
The generic connector tool surface lets agents discover and run brokered tools, while the eval manifest supplies safe fake email and calendar connectors.

### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `tool handling`

A connector broker is a server that knows about one family of external tools and holds the user's connected account token. This file exposes four agent-facing tools: list available connectors, describe a connector's tools, search for the right tool, and call a chosen tool. The agent first asks what connectors exist, then asks a broker what real tool names and input shapes are available, and only then executes one.

The important safety idea is that the agent does not directly hold service credentials. When a tool is called, this file asks the current turn's connector registry which broker owns the requested connector, picks the right connected account, and sends the request to that broker.

It also bridges files. If an argument contains a special workspace file marker, the file is checked inside the sandbox, uploaded to the broker's file store through a temporary upload URL, and replaced with the broker's own file reference. If the broker returns files, they are downloaded back into the workspace under a fresh connector_files folder. Like a mailroom, this file routes packages between the local workspace and external services while keeping credentials and raw file transfer out of the main server process.

#### Function details

##### `list_external_tools`  (lines 106–120)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Lists connector sources that are available in the current turn, such as GitHub or Gmail. It helps the agent discover which outside services it can use before asking for specific tools.

**Data flow**: It receives the current tool context and search keywords. It reads the connector registry from the context, compares each query with connector IDs and labels, removes duplicates, and returns a JSON result containing matching source IDs and human-readable labels.

**Call relations**: This is one of the public tool handlers registered in CONNECTOR_TOOLS. When called, it first relies on _registry to find the turn's connector list, then hands the final connector list to _json_result so the tool system receives a normal ToolResult.

*Call graph*: calls 2 internal fn (_json_result, _registry).


##### `describe_external_tools`  (lines 123–145)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector and, when needed, helps discover matching tool names. This prevents the agent from guessing tool names that the broker may not support.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional search query. It asks the matching broker for schemas for exact names, records any names the broker does not recognize, optionally asks the broker for nearby available tools, and returns JSON containing schemas, available alternatives, and unresolved names.

**Call relations**: This public tool handler is normally called after list_external_tools and before call_external_tool. It uses _registry to find the broker, _tool_json to turn broker tool objects into plain JSON-friendly dictionaries, _discovery_query to build a fallback search when guessed names fail, and _json_result to package the answer.

*Call graph*: calls 4 internal fn (_discovery_query, _json_result, _registry, _tool_json).


##### `call_external_tool`  (lines 148–152)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one external connector tool through its broker. It is the execution step after the agent has already found the correct tool and learned its input schema.

**Data flow**: It receives the requested connector, tool name, optional account ID, and arguments. It finds the connector entry, resolves which connected account to use, creates a _ConnectorCall object, runs it, and wraps the broker's response as JSON.

**Call relations**: This public tool handler is the gateway from the agent to an external service. It uses _registry to locate the broker, ToolContext.connector_account to select the authorized account, then delegates the detailed staging, execution, and file fetching work to _ConnectorCall.run before returning through _json_result.

*Call graph*: calls 3 internal fn (connector_account, _json_result, _registry); 1 external calls (__init__).


##### `_ConnectorCall.run`  (lines 166–177)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> dict[str, object]
```

**Purpose**: Carries out one connector tool call from start to finish. It prepares input files, asks the broker to execute the tool, and brings any produced files back into the workspace.

**Data flow**: It receives the tool arguments and the chosen connected account ID. It walks through the arguments and replaces workspace file references with broker-ready file references, sends the staged arguments to the broker's execute API, asks the broker which output files were produced, downloads those files, and returns the broker response with workspace file paths added when files exist.

**Call relations**: call_external_tool creates a _ConnectorCall and then calls this method. Inside the execution story, run first hands each argument value to _staged_value, then calls the broker, then hands broker-reported output files to _fetched_files so the final result can mention local workspace paths.

*Call graph*: calls 2 internal fn (_fetched_files, _staged_value).


##### `_ConnectorCall._staged_value`  (lines 179–194)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Recursively looks through an argument value for workspace file references and stages those files for the broker. It lets deeply nested arguments contain files, not just top-level fields.

**Data flow**: It receives one argument value, which may be a plain value, a list, a dictionary, or a special dictionary containing only the workspace_file key. Plain values pass through unchanged, lists and dictionaries are processed item by item, and workspace file markers are replaced with the result of uploading that file to the broker's file store.

**Call relations**: _ConnectorCall.run calls this while preparing arguments before execution. When it finds an actual workspace file marker, it delegates the file-specific work to _stage_file; otherwise it keeps walking through the data structure until every nested value is safe to send to the broker.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 196–227)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the connector broker's file storage, or skips the upload if the broker already has the same file. This makes local files usable as inputs to remote connector tools.

**Data flow**: It receives a workspace path string. It converts it to a scoped workspace path, runs a small hash-and-size check inside the sandbox, rejects unreadable or too-large files, guesses the file type from the name, asks the broker for an upload destination, uploads the bytes with curl if needed, and returns the broker's argument value for that staged file.

**Call relations**: _staged_value calls this whenever it finds a workspace_file argument. It uses sandbox commands for hashing and uploading so file bytes travel from the sandbox to the broker's storage, not through the main server, and it uses helper libraries to quote shell arguments, inspect filenames, and guess MIME type.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 229–248)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool back into the workspace. It gives the agent local paths it can use in later steps.

**Data flow**: It receives broker file records, each with a name and temporary download URL. For each file, it chooses a safe filename, creates a unique target folder under /workspace/connector_files, downloads the file with curl inside the sandbox, and returns a list of dictionaries containing the saved name and workspace path.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the file list reported by the broker. This method completes the round trip: after _stage_file moves input files outward, _fetched_files moves output files back inward.

*Call graph*: called by 1 (run); 3 external calls (PurePosixPath, quote, uuid4).


##### `search_connector_tools`  (lines 251–262)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Searches inside one connector for tools that match a plain-language goal. It is useful when the agent knows what it wants to do but does not know the exact connector tool name.

**Data flow**: It receives a connector source ID and search query. It finds the matching broker, asks that broker for semantic search results, converts each returned tool into JSON-friendly form, and returns tools plus any broker-provided plan, guidance, and warnings.

**Call relations**: This public tool handler is another discovery path alongside describe_external_tools. It uses _registry to find the broker, _tool_json to shape each result, and _json_result to send the structured search answer back to the tool caller.

*Call graph*: calls 3 internal fn (_json_result, _registry, _tool_json).


##### `_registry`  (lines 265–268)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Fetches the connector registry for the current turn. The registry is the map that says which connector IDs exist and which broker owns each one.

**Data flow**: It receives the tool context and reads ctx.connectors. If the registry is missing, it raises an error because connector tools cannot work without knowing the available brokers; otherwise it returns the registry unchanged.

**Call relations**: All four public handlers call this near the start. It is the shared doorway from tool calls into the current turn's connector setup, so list, describe, search, and execute all agree on the same available connectors.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 271–272)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Turns a broker tool object into a simple dictionary that can be returned as JSON. It keeps only the fields the agent needs: the tool name, description, and input schema.

**Data flow**: It receives a BrokerTool object. It reads its slug, description, and input_schema fields and returns them in a plain dictionary.

**Call relations**: describe_external_tools uses this when returning exact tool schemas, and search_connector_tools uses it when returning search results. It is a small formatting helper between broker-specific objects and agent-readable JSON.

*Call graph*: called by 2 (describe_external_tools, search_connector_tools).


##### `_discovery_query`  (lines 275–282)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Builds a search query for tool discovery when the caller did not provide one directly. It turns failed guessed tool names into useful keywords.

**Data flow**: It receives an explicit query string and a list of unresolved tool names. If the explicit query is present, it returns that. Otherwise it lowercases unresolved names, replaces punctuation-like characters with spaces, removes repeated words while preserving order, and returns the resulting keyword string.

**Call relations**: describe_external_tools calls this when it needs to ask the broker for available alternatives. It uses regular expression substitution to turn machine-like slugs into plain search words, helping a bad guess lead to real tool suggestions.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 285–286)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Packages a Python dictionary as the text content of a ToolResult. This gives every public connector tool the same JSON response shape.

**Data flow**: It receives a payload dictionary. It serializes the payload to a JSON string, wraps that string in TextContent, then wraps the content in a ToolResult and returns it.

**Call relations**: list_external_tools, describe_external_tools, search_connector_tools, and call_external_tool all finish by calling this. It is the final formatting step that turns their internal answers into something the tool framework can send back to the agent.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation tool discovery and tool execution`

This file builds an evaluation-only connector extension. Its job is to give tests a realistic mailbox and calendar without depending on Gmail, Outlook, or any outside service. Think of it like a practice kitchen: the assistant uses the same doors, tools, and routines it would use in production, but the food is fake and fully inspectable afterward.

The file declares two database tables: one for emails and one for calendar events. Evaluation setup can seed these tables with starting data. During a conversation, the assistant discovers tools, reads their schemas, and calls them through the normal broker interface. The broker then writes sent emails, lists messages, creates events, updates events, or cancels events in those same tables. Afterward, a grader can check the final table contents to see what really happened.

The file also defines the advertised tools and their input shapes, using validation models so bad arguments are rejected early. Email supports sending and listing. Calendar supports creating, listing, updating, and cancelling events. Calendar cancellation does not delete the row; it marks the event as cancelled, which makes the end state easy to inspect.

Finally, the manifest function registers two connector providers, one for eval email and one for eval calendar. A small OAuth stub is included because the connector registry expects one, even though evaluations seed access directly rather than using a real sign-in flow.

#### Function details

##### `_transaction`  (lines 147–151)

```
def _transaction()
```

**Purpose**: Opens a database transaction scoped to this evaluation extension. The broker uses it whenever it needs to read or change the eval email and calendar tables safely.

**Data flow**: It takes no direct input. It creates an extension context with this extension's scoped store and no declared credentials, then asks that context for a transaction. The result is a database transaction object that callers use with `async with` to run queries and commit or roll back as one unit.

**Call relations**: All database-reading and database-writing broker methods call this helper before touching the eval tables. It centralizes how the extension gets its workspace-scoped storage, so sending email, listing email, creating events, listing events, and changing events all use the same storage path.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 154–158)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a real date-time value. If the string has no time zone, it treats it as UTC so stored event times are always timezone-aware.

**Data flow**: It receives a text timestamp such as `2026-01-01T09:00:00`. It parses that text into a date-time object. If the parsed value has no timezone attached, it adds UTC. It returns the normalized date-time object for database storage.

**Call relations**: Event creation and event updating call this before writing start or end times. That keeps calendar rows consistent no matter whether the assistant supplied an explicit timezone.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 166–174)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for a provider, such as eval email or eval calendar. It can also filter the list by a search word, while falling back to the full list if nothing matches.

**Data flow**: It receives a workspace id, a provider name, and a query string. It looks up that provider's tool catalog, lowercases and trims the query, and compares it with tool names and descriptions. It returns matching tool descriptions, or the full catalog when the query is empty or finds no match.

**Call relations**: The broker's search method calls this when the connector system asks what tools are available. It is part of the discovery step before any tool is actually run.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 176–180)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the full description and input schema for one named tool. This lets the assistant know exactly what arguments a tool expects before calling it.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans that provider's catalog for a matching slug. If found, it returns the matching tool description. If not found, it raises an unknown-tool error.

**Call relations**: This fits into the connector description flow, after tools have been listed and before execution. It does not call the database; it only reads the in-memory catalog defined in this file.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 182–219)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a requested eval email or calendar tool. It is the main dispatcher that turns a generic connector call into the specific action, such as sending an email or updating an event.

**Data flow**: It receives the workspace id, provider name, tool slug, raw argument data, account id, and an optional idempotency key. It checks which provider and slug were requested, validates the raw arguments against the right input model, then calls the matching helper method. It returns that helper's result as a plain dictionary, or raises an unknown-tool error if the request does not match the catalog.

**Call relations**: This is called by the connector execution path when the assistant invokes a tool. It hands off to the private email and calendar methods that actually read or change the eval tables.

*Call graph*: calls 6 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 221–236)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Records a sent email in the eval mailbox. It does not contact a real mail server; it writes a durable row that later graders can inspect.

**Data flow**: It receives a workspace id and validated send-email arguments. It creates a new email id, opens a transaction, and inserts a row into the email table with folder `sent`, the fixed assistant sender address, recipients, subject, body, and the current time. It returns the new id, sent status, and recipient list.

**Call relations**: The execute dispatcher calls this when the `send_email` tool is invoked for the email provider. It relies on `_transaction` for database access and is one of the main ways a conversation leaves an auditable result.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 238–273)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the eval mailbox. It can list inbox or sent messages, optionally narrowed by a text search.

**Data flow**: It receives a workspace id and validated listing arguments. It builds database conditions for the workspace and folder, adds a case-insensitive match over sender, subject, and body when a query is present, then selects rows newest first up to the requested limit. It returns a dictionary containing email objects with ids, sender, recipients, subject, body, and sent time.

**Call relations**: The execute dispatcher calls this for the `list_emails` tool. It uses `_transaction` to read the extension table and gives the assistant a normal-looking mailbox view backed by seeded or previously written eval data.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 275–289)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Adds a confirmed calendar event to the eval calendar. This gives the assistant a way to schedule something in the controlled test environment.

**Data flow**: It receives a workspace id and validated event creation arguments. It creates a new event id, parses the start and end time strings into date-time values, opens a transaction, and inserts a calendar row with title, times, attendees, and status `confirmed`. It returns the new id and status.

**Call relations**: The execute dispatcher calls this when the `create_event` tool is invoked for the calendar provider. It uses `_moment` to normalize times and `_transaction` to persist the new row.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 291–304)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Reads calendar events from the eval calendar. It can optionally filter by title and returns events in start-time order.

**Data flow**: It receives a workspace id and validated listing arguments. It builds a query for that workspace, adds a case-insensitive title filter if requested, selects matching rows ordered by start time, and limits the count. It converts each database row into a simple event dictionary and returns them under `events`.

**Call relations**: The execute dispatcher calls this for the `list_events` tool. It uses `_transaction` for the database read and `_event_json` so listed events use the same output shape as updated or cancelled events.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 306–318)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares changes for an existing calendar event. It supports changing only the fields the caller provided, such as title, times, or attendees.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary from the non-empty fields, parsing time strings when start or end is supplied. If no change was requested, it raises an error. Otherwise, it passes the event id and changes to `_change_event` and returns the updated event data.

**Call relations**: The execute dispatcher calls this for the `update_event` tool. It does the argument-to-database-field translation, then hands the actual database update to `_change_event`.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 320–321)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Cancels an existing calendar event by marking it cancelled. It keeps the event row instead of deleting it so the final state remains visible.

**Data flow**: It receives a workspace id and validated cancel arguments containing an event id. It creates a small change request that sets the event status to `cancelled`, then asks `_change_event` to apply it. The output is the updated event dictionary.

**Call relations**: The execute dispatcher calls this for the `cancel_event` tool. It is a thin wrapper around `_change_event`, reusing the same safe update-and-return behavior as normal event edits.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 323–342)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the fresh version. It also checks that the event belongs to the current workspace, which prevents one workspace from changing another's data.

**Data flow**: It receives a workspace id, an event id string, and a dictionary of database fields to change. It opens a transaction, updates the matching event row for that workspace, and checks that exactly one row changed. If not, it raises an error saying the event was not found in this calendar. It then reads the updated row and returns it as a plain event dictionary.

**Call relations**: Both `_update_event` and `_cancel_event` call this after deciding what should change. It uses `_transaction` for the database work and `_event_json` to format the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 344–352)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a calendar database row into the plain dictionary returned by calendar tools. This keeps event output consistent across listing, updating, and cancelling.

**Data flow**: It receives one database row. It pulls out the id, title, start time, end time, attendees, and status, converts the id and times to strings, and returns a simple dictionary suitable for tool responses.

**Call relations**: `_list_events` calls this for each event it returns, and `_change_event` calls it after editing or cancelling an event. It is the shared formatter for calendar results.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 354–355)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Says that eval email and calendar tools never produce downloadable files. The connector interface asks for this, but these providers only return structured data.

**Data flow**: It receives a tool response dictionary but does not need to inspect it. It always returns an empty tuple, meaning there are no file outputs to attach.

**Call relations**: This supports the broker interface used by the wider connector system. Unlike tools that might generate documents or attachments, the eval environment has nothing to hand off here.


##### `EvalEnvBroker.stage_upload`  (lines 357–366)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for these eval providers. Email and calendar evaluation tools are deliberately limited to text and event data.

**Data flow**: It receives workspace, provider, tool slug, filename, MIME type, and checksum information for a proposed upload. Instead of storing or staging anything, it raises an error explaining that eval providers accept no file uploads.

**Call relations**: This exists because the broker interface includes upload support. If any caller tries to use that path with the eval providers, this method stops it immediately rather than pretending uploads are supported.


##### `EvalEnvBroker.search`  (lines 368–369)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool search results in the standard search response object expected by the connector system. It lets callers search for available eval tools by text.

**Data flow**: It receives a workspace id, provider name, and query string. It asks `tools` for the matching tool descriptions, then places those tools into a broker search result. The output is a structured search response.

**Call relations**: This is part of tool discovery. It delegates the actual matching to `EvalEnvBroker.tools`, then packages the result in the format the connector layer expects.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 371–372)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple fake credential for an eval account. It satisfies the connector system's need for a credential without using any real secret.

**Data flow**: It receives a workspace id, provider name, and account id. It builds a bearer token string that includes the account name and returns it as a credential object. It does not read a secret store or contact an authentication service.

**Call relations**: The wider connector path can call this when it needs credentials before dispatching a tool. In this eval extension, the credential is only a marker that keeps the normal flow intact.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 383–384)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a placeholder authorization URL for the eval provider. It exists because providers need an OAuth-style sign-in descriptor, even though eval runs do not normally use it.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider's fake host into an authorization URL string. Nothing is stored or sent over the network by this method.

**Call relations**: The manifest attaches `_EvalEnvOAuth` objects to the email and calendar providers. If a connect flow ever asks for an authorization URL, this method gives a realistic-looking but eval-only URL.


##### `_EvalEnvOAuth.exchange`  (lines 386–389)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange by returning the fixed eval account id. OAuth is the common web sign-in flow where a temporary code is exchanged for account access; here it is only a stub.

**Data flow**: It receives a code, redirect URI, workspace id, and state. It ignores the details and returns an OAuth account object with the constant eval account id. It does not validate the code or fetch tokens.

**Call relations**: This supports the provider descriptor required by the registry. Normal evaluations seed grants directly, but if the exchange path is driven, it still returns a consistent eval account.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 392–409)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest that registers the eval email and eval calendar connectors. This is the entry the host system uses to discover what this extension provides.

**Data flow**: It creates one shared `EvalEnvBroker`, then builds a manifest containing two connector provider entries. Each provider gets a fake OAuth descriptor, a human label, and the shared broker. The returned manifest names the extension and version and exposes both eval providers to the assistant evaluation pack.

**Call relations**: The extension loader calls this when registering the extension. It wires together the OAuth stubs and broker so later discovery and tool calls can reach the email and calendar behavior defined in this file.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Search provider transport
The Exa extension adapts the system’s standard search and fetch requests to Exa’s external web-search API.

### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `request handling`

This file is an adapter between UFO’s research tools and Exa, an outside search API. The rest of the system does not need to know Exa’s exact web address, request format, API key header, or response shape. It can ask for “search results” or “the contents of this page,” and this file translates that into Exa’s language.

The important safety point is where the API key lives. The Exa key is read on the host side through `CredentialAccess`, which means the raw secret is not sent into the sandboxed tool environment. In everyday terms, the extension is like a librarian who has the library card in a locked drawer: tools can ask the librarian to search, but they never get to hold the card themselves.

`ExaSearchProvider` is the main piece. Its `search` method builds a search request, sends it to Exa’s `/search` endpoint, checks that the reply really contains results, and converts each result into the system’s `SearchHit` format. Its `fetch` method asks Exa’s `/contents` endpoint to read a specific URL and return text or a summary. Shared helpers validate Exa’s response and safely pull optional strings from it. The `manifest` function declares this extension to the host system, including the credential slot and the search backend name.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Exa and returns results in the project’s normal search-result format. Use this when a tool asks the selected search provider to search the web.

**Data flow**: It receives a `SearchQuery`, which includes the search text and options such as result count, allowed domains, recency, or a special vertical. It turns that query into an Exa request body, sends it to Exa, checks the returned payload for a results list, converts each raw Exa item into a `SearchHit`, and returns a `SearchResults` object containing those hits.

**Call relations**: This is the main search entry point for the provider. It calls `_search_body` to speak Exa’s request format, `_post` to actually contact Exa, `_results` to reject malformed replies, and `_hit` to translate each Exa result into the shared format the rest of the system expects.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable page content for one URL through Exa. It is used when the system already has a link and wants the page text, and optionally a summary focused on a prompt.

**Data flow**: It receives a `FetchRequest` with a URL, optional character limit, optional summary prompt, and a flag asking for a fresh crawl. It builds an Exa `/contents` request, caps the text length to a safe maximum, sends the request, pulls the first returned item if present, and returns a `FetchedPage` with the URL, text, and optional summary.

**Call relations**: This is the provider’s page-reading path. It calls `_post` to reach Exa’s contents endpoint, `_results` to verify the response shape, and `_opt_str` to include a summary only when Exa actually returned one as text.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–91)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON request body Exa expects for a search. It hides Exa-specific option names from the rest of the system.

**Data flow**: It receives a `SearchQuery`. For normal searches, it asks Exa for text snippets and highlights, may restrict results to allowed domains, and may compute a start date for recent results. For vertical searches, such as academic or people search, it uses shorter text and maps the project’s vertical name to Exa’s category name when possible. It returns a dictionary ready to send as JSON.

**Call relations**: `search` calls this before making the HTTP request. This helper does not contact Exa itself; it only prepares the body that `_post` will send.

*Call graph*: called by 1 (search); 2 external calls (now, timedelta).


##### `ExaSearchProvider._hit`  (lines 94–104)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Converts one raw Exa search result into the project’s `SearchHit` format. This keeps the rest of the system from depending on Exa’s field names.

**Data flow**: It receives one dictionary from Exa. It reads fields such as URL, title, text, published date, and highlights, replacing missing basic fields with empty strings and keeping highlights only when they are real strings. It returns a `SearchHit` object.

**Call relations**: `search` calls this once for each item returned by `_results`. It uses `_opt_str` for the published date so that non-text values do not leak into the shared search model.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 106–114)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends one authenticated HTTP POST request to Exa and returns the decoded JSON reply. It is the single place where the Exa API key is read and placed into the request header.

**Data flow**: It receives an API path, such as `/search` or `/contents`, and a JSON-ready request body. It reads the Exa API key from the configured credential slot, creates an async HTTP client pointed at `api.exa.ai`, posts the body with the key header, and returns the parsed JSON response. If Exa returns an error status, it raises `ExaError` with the status and response text.

**Call relations**: Both `search` and `fetch` call this when they are ready to talk to Exa. It is also designed for tests: the optional transport can be replaced with a fake HTTP transport so tests do not need real network calls.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 117–121)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Checks that an Exa response contains a usable `results` list. It prevents a broken or unexpected API reply from being mistaken for an empty successful search.

**Data flow**: It receives the decoded response payload from Exa. If the payload is a dictionary with a list under `results`, it keeps only the entries that are dictionaries and returns them. If there is no proper results list, it raises `ExaError`.

**Call relations**: `search` and `fetch` call this immediately after `_post`. It acts as a gatekeeper before the provider tries to convert Exa’s response into `SearchHit` or `FetchedPage` objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 124–125)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only if it is actually a string. It is a small safety helper for optional text fields that may be missing or have the wrong type.

**Data flow**: It receives any value. If the value is text, it returns that text; otherwise it returns `None`. It does not change anything else.

**Call relations**: `_hit` uses it for optional published dates, and `fetch` uses it for optional summaries. This keeps questionable API fields from being treated as valid strings.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 128–141)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It declares the Exa credential slot and registers Exa as a selectable search provider backend.

**Data flow**: It creates a `Manifest` with the extension name and version, one credential slot named for the Exa API key, and one search provider specification. That provider specification says that when the backend name is `exa`, the system can build an `ExaSearchProvider` using the available credentials.

**Call relations**: The host calls this during extension discovery or setup. The returned manifest is what lets configuration such as selecting the `exa` search provider connect to the `ExaSearchProvider` class in this file.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### External action providers
MCP and Pipedream integrations expose externally hosted tools and app actions through safe discovery and execution paths.

### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

MCP, or Model Context Protocol, is a standard way for outside services to offer tools to an AI agent. This file is the bridge between the agent and those outside MCP servers. Without it, the agent would not be able to discover tools that a workspace has configured, or call those tools at run time.

A workspace stores its MCP server list in a credential slot named `mcp_servers`. Think of this like an address book: each entry has a friendly name, a URL, and optionally an auth token. The file validates that each server URL is HTTP or HTTPS, then creates a client that can speak MCP over HTTP.

The main flow has two steps. First, `_list_mcp_tools` looks up a named server, connects to it, asks what tools it offers, and returns their names, descriptions, input shapes, and whether they look safe to repeat. Second, `_call_mcp_tool` looks up the same kind of server, checks that the requested arguments are not too large, calls the named tool, and turns the response into a `ToolResult` the agent can read.

The file is careful about trust. MCP servers are external, so their output may be unsafe or misleading. The manifest marks both exposed tools as untrusted, and the code enforces a one-mebibyte size limit on outgoing arguments and incoming results instead of silently cutting data off.

#### Function details

##### `McpServer._http_url`  (lines 70–73)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates the URL for a configured MCP server. It makes sure the system only accepts ordinary HTTP or HTTPS URLs, rather than an unexpected kind of address.

**Data flow**: It receives a URL string from the MCP server configuration. It checks the string against a simple HTTP/HTTPS pattern. If the URL is valid, it returns the same string; if not, it raises an error so the bad server entry is rejected.

**Call relations**: This runs automatically when an `McpServer` configuration object is built. It protects later calls, such as listing tools or calling a tool, from starting with an invalid server address.


##### `mcp_client`  (lines 95–101)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This builds a ready-to-use MCP client for one configured server. It is the small doorway through which the rest of the file talks to an external MCP service.

**Data flow**: It receives an `McpServer` object containing a URL and maybe an auth token. It turns the token into an HTTP `Authorization` header when present, creates a streamable HTTP transport for the server URL, and wraps that transport in a FastMCP client with a timeout. The result is a client object that can list and call tools.

**Call relations**: _list_mcp_tools and `_call_mcp_tool` call this after `_server` has found the requested server. The FastMCP library then takes over the protocol details, such as connecting, initializing the MCP session, and sending the actual list or call request.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 104–116)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This finds one named MCP server from the workspace’s stored credentials. It prevents blind calls by refusing to continue when the extension context, credential slot, or requested server name is missing.

**Data flow**: It receives the current tool context and a server name. It reads the `mcp_servers` credential value from the extension context, parses it as JSON into validated server objects, and searches the map for the requested name. It returns the matching `McpServer`, or raises a clear error if it cannot.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` start by calling `_server`. This makes server lookup a shared first step before any network client is created or any MCP request is sent.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 119–135)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the implementation behind the agent-facing `list_mcp_tools` tool. It asks a configured MCP server what tools it offers, so the agent can choose a real tool name and use the correct input shape instead of guessing.

**Data flow**: It receives the tool context and input containing a server name. It looks up that server, opens an MCP client connection, asks the server for its tool list, and then reshapes each returned tool into plain JSON fields: name, description, input schema, and an idempotent flag. It returns that JSON as a tool result.

**Call relations**: This function is registered in `manifest` as the handler for `list_mcp_tools`. In its flow, it calls `_server` to resolve the configured endpoint, `mcp_client` to connect to it, and `_json_result` to package the discovered catalog for the agent.

*Call graph*: calls 3 internal fn (_json_result, _server, mcp_client).


##### `_call_mcp_tool`  (lines 138–150)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the implementation behind the agent-facing `call_mcp_tool` tool. It invokes one specific tool on a configured MCP server and turns the external response into a result the agent can consume.

**Data flow**: It receives the tool context plus a server name, exact tool name, and JSON arguments. It looks up the server, serializes the arguments to check they are no larger than the request limit, connects to the MCP server, and calls the named tool. If the server reports an error, it returns an error tool result with bounded text. If the server returns structured JSON content, it returns that; otherwise it joins any text blocks and returns them in a JSON wrapper.

**Call relations**: This function is registered in `manifest` as the handler for `call_mcp_tool`. It relies on `_server` for configuration lookup, `mcp_client` for the network connection, `_joined_text` to collect text responses, `_bounded` to enforce the response size limit, and `_json_result` to package normal JSON results.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 153–154)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This extracts readable text from an MCP response that may contain several content blocks. It ignores non-text blocks and joins the text blocks with new lines.

**Data flow**: It receives a list of content objects from an MCP result. It keeps only objects that are MCP text content, takes their text, joins those pieces with newline characters, and returns the combined string.

**Call relations**: _call_mcp_tool` uses this when an MCP tool fails or when the result is not structured JSON. It acts like a simple text collector before the response is bounded and returned to the agent.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 157–160)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for text returned through this extension. It fails loudly if a response is too large, instead of silently cutting it short and risking a misleading partial answer.

**Data flow**: It receives a text string. It measures the string as bytes, because network and storage limits are byte-based. If the text is within the one-mebibyte limit, it returns the original text; if it is too large, it raises an `McpError`.

**Call relations**: _call_mcp_tool` uses this for error text, and `_json_result` uses it for JSON results. This makes response size checking a shared final gate before data is handed back to the agent.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_json_result`  (lines 163–164)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This turns a JSON-like dictionary into the standard tool result format used by the extension system. It also applies the response size limit before returning anything.

**Data flow**: It receives a dictionary payload. It serializes that dictionary into a JSON string, passes the string through `_bounded`, wraps the safe text in `TextContent`, and returns a `ToolResult` containing it.

**Call relations**: _list_mcp_tools` uses this to return a discovered tool catalog, and `_call_mcp_tool` uses it to return successful tool output. It is the shared packaging step for normal, non-error responses.

*Call graph*: calls 1 internal fn (_bounded); called by 2 (_call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 167–198)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the host system: its name, version, available tools, and required credential slot. It is how the rest of the application learns that this MCP tool pack exists.

**Data flow**: It takes no input. It builds a `Manifest` containing two tool definitions, `list_mcp_tools` and `call_mcp_tool`, each with its description, input model, handler function, and untrusted-content marking. It also declares the `mcp_servers` credential slot that stores the workspace’s MCP server address book.

**Call relations**: The host extension loader calls `manifest` when registering this extension. The returned manifest connects agent-visible tool names to `_list_mcp_tools` and `_call_mcp_tool`, and tells the host that credentials must be available for server lookup.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

This broker is the shared doorway that every Pipedream-backed connector uses. Without it, the agent could know that a user connected an app, but it would not know which Pipedream actions exist, what inputs those actions need, how to attach the user’s account, or how to recover when an old connection has gone stale.

The main class, `PipedreamBroker`, is deliberately stateless. Each method asks for a fresh Pipedream client when it runs. That matters because tests or deployments can swap the underlying network transport, and this broker will automatically use the current one instead of holding onto an old connection.

The broker first translates a project-level provider name into Pipedream’s app name. It can list actions as tools, fetch one action’s full definition, and turn Pipedream’s configurable fields into a plain JSON schema. It hides internal fields, including the connected-account slot, because the broker fills that in itself.

When executing an action, it checks that the requested account belongs to the right workspace and app, inserts the account into the action’s app slot, and calls Pipedream’s server-side run API. If Pipedream says the action or account is missing, the broker turns that into clearer guidance, such as telling the user to reconnect. It also extracts files saved by the action through Pipedream’s file stash and exposes them as downloadable URLs.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–67)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for one provider and presents them as tools the agent can choose from. If a search phrase finds nothing, it falls back to the app’s general top actions so the agent is not left with an empty list just because Pipedream’s search is strict.

**Data flow**: It receives a workspace id, provider name, and search text. It looks up the provider’s Pipedream app, asks Pipedream for matching actions, converts the returned action records into simple broker tools, and returns those tools as a tuple. If the first search has no results and the query was not empty, it repeats the request with an empty query.

**Call relations**: This is the main catalog lookup used by the broker. `PipedreamBroker.search` calls it when the wider connector system asks for searchable tools, and it relies on `_spec` to identify the Pipedream app and `_listed_tools` to turn Pipedream’s response into the project’s tool shape.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 69–75)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one Pipedream action. The result tells the agent what arguments it may provide, while leaving out fields that the broker or Pipedream must fill internally.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, reads its configurable properties, converts those properties into a JSON schema, and returns a `BrokerTool` containing the slug, description, and input schema.

**Call relations**: This is used when the system needs details for one specific tool before calling it. It gets the raw action definition through `_definition`, extracts usable properties with `_props`, turns them into a schema with `_input_schema`, and safely reads text with `_str`.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 77–112)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It makes sure the account is valid for the requested app, binds that account into the action, sends the run request to Pipedream, and turns common failure cases into clearer errors.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, connected account id, and an optional idempotency key. It fetches the action definition, copies the arguments, adds the account information into the action’s app field, verifies the account belongs to the workspace and app, and asks Pipedream to run the action. It returns Pipedream’s response, or raises a clear error if the action is unknown, the account is stale, the account belongs to another app, or the action reports its own error.

**Call relations**: This is the broker’s central execution path. It uses `_definition` to understand the action, `_app_slot` to find where the connected account must go, `_spec` to check the app, `_key_miss` to improve unknown-action errors, and `_stale_account` plus `_reconnect_error` to tell the agent when the user needs to reconnect.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 114–132)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files that a Pipedream action saved during its run and turns them into downloadable broker file records. This lets the sandbox fetch output files without needing direct access to Pipedream’s temporary file system.

**Data flow**: It receives the action response dictionary. It looks inside the response exports for Pipedream’s file stash upload list, ignores malformed entries, pulls out each download URL and local file name, and returns a tuple of `BrokerFile` objects.

**Call relations**: This is used after an action has run and may have produced files. It does not call back to Pipedream; it simply reads the response that `PipedreamBroker.execute` returned and converts the file-stash entries into the connector system’s file output format.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 134–146)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged file uploads for Pipedream actions. Pipedream actions expect file inputs as URLs, so the correct path is to share a workspace file and pass its download link.

**Data flow**: It receives details about a file that someone wants to stage for upload. Instead of creating an upload target, it raises a `ValueError` explaining that Pipedream needs a URL-based file input.

**Call relations**: This protects callers from using the wrong file-transfer method. Other connector brokers may support staged uploads, but this Pipedream broker intentionally stops that flow and points the caller toward `share_file` instead.


##### `PipedreamBroker.search`  (lines 148–149)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool lookup in the connector system’s search result shape. Pipedream does not provide separate routing guidance here, so the search result mainly contains tools.

**Data flow**: It receives a workspace id, provider name, and query. It asks `PipedreamBroker.tools` for matching actions and places those tools into a `BrokerSearch` object, which it returns.

**Call relations**: This is the search-facing entry for the wider connector interface. It delegates the real catalog work to `PipedreamBroker.tools` and then packages the result in the expected broker search format.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 151–172)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can proxy network requests through Pipedream for a connected account. It first proves that the account belongs to the workspace and the expected app.

**Data flow**: It receives a workspace id, provider name, and connected account id. It looks up the provider’s app, fetches the workspace account from Pipedream, handles missing accounts as reconnect-needed errors, rejects accounts for the wrong app, and returns a `Credential` whose transport sends requests through `PipedreamProxyTransport`.

**Call relations**: This is used when code needs an authenticated transport rather than a server-side action run. It uses `_spec` to know which app is expected, `_reconnect_error` when the account is gone, and wraps the current Pipedream client transport so tests and deployment overrides are respected.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 174–182)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for one action slug. It turns Pipedream’s not-found response into the broker’s unknown-tool signal.

**Data flow**: It receives an action slug. It asks the current Pipedream client for that action’s definition, raises `UnknownBrokerTool` if Pipedream returns a not-found error, and otherwise returns the definition dictionary, unwrapping a `data` field when Pipedream uses that shape.

**Call relations**: Both `PipedreamBroker.schema` and `PipedreamBroker.execute` depend on this helper before they can understand an action. It is the common gate between a simple action key and the detailed Pipedream metadata needed for schemas and account binding.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 184–198)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when the agent tries to run an action slug that does not exist. Instead of only saying “not found,” it tries to include real available action keys for that app.

**Data flow**: It receives a Pipedream client, provider name, and missing slug. It looks up the provider’s app, tries to list the app’s available actions, formats their slugs into a message, and returns a `PipedreamError`. If listing actions fails, it returns a simpler not-found error.

**Call relations**: `PipedreamBroker.execute` calls this after `_definition` reports an unknown tool. It uses `_spec` to identify the app and `_listed_tools` to turn the catalog response into readable action names, giving the model better information for its next attempt.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 201–208)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Checks whether a Pipedream error looks like it was caused by an old or missing connected account. This helps the system distinguish “the user needs to reconnect” from ordinary provider errors.

**Data flow**: It receives a `PipedreamError` and the account id that was used. It lowercases the error body and looks for narrow signs such as “external user not found” or the exact account id appearing with “not found.” It returns `True` only for those likely stale-account cases, otherwise `False`.

**Call relations**: `PipedreamBroker.execute` uses this after Pipedream run failures and action-level errors. When it returns true, the execution path passes the error to `_reconnect_error` so the agent can guide the user to reconnect.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 211–212)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds user-facing reconnect guidance to a Pipedream error. It keeps the original status and message, but appends advice that the connected account grant is stale.

**Data flow**: It receives a `PipedreamError` and provider name. It asks the shared connector helper for stale-grant guidance, appends that guidance to the error body, and returns a new `PipedreamError` with the same status code.

**Call relations**: `PipedreamBroker.execute` uses this when an action run points to a stale account, and `PipedreamBroker.credential` uses it when the stored account cannot be found. It is the final step that turns a low-level missing-account error into something the agent can explain to the user.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 215–219)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up this project’s registered Pipedream connector settings for a provider name. In practice, it answers “which Pipedream app does this provider mean?”

**Data flow**: It receives a provider string. It searches the Pipedream connector registry for that provider and returns its connector specification. If the provider is not registered, it raises a `KeyError`.

**Call relations**: Several broker paths call this before talking to Pipedream: tool listing, execution, credential creation, and helpful missing-key errors. It keeps provider-to-app translation in one place so the rest of the broker can speak in Pipedream app slugs.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 222–234)

```
def _listed_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream’s action-list response into the project’s simple broker tool records. It keeps only usable action keys and short descriptions.

**Data flow**: It receives a dictionary returned by Pipedream’s list-actions API. It reads the `data` list, skips entries that are not dictionaries or do not have a non-empty string key, creates a `BrokerTool` for each valid action, and returns them as a tuple.

**Call relations**: `PipedreamBroker.tools` uses this for normal catalog searches, and `PipedreamBroker._key_miss` uses it to show available actions after an unknown slug. It uses `_str` so missing or non-text descriptions become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 237–239)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable property records from a Pipedream action definition. These properties describe what fields the action can accept.

**Data flow**: It receives an action definition dictionary. It reads the `configurable_props` value, keeps only entries that are dictionaries, and returns them as a list. If the field is absent or not a list, it returns an empty list.

**Call relations**: `PipedreamBroker.schema` uses this before building the user-visible input schema, and `_app_slot` uses it to find the special account-binding field. It is a small cleanup step between Pipedream’s raw metadata and the broker’s stricter expectations.

*Call graph*: called by 2 (schema, _app_slot).


##### `_app_slot`  (lines 242–249)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special property in a Pipedream action where the connected account must be inserted. If an action has no such slot, it cannot be safely run for a user account.

**Data flow**: It receives an action definition and slug. It scans the action’s configurable properties for a property whose type is Pipedream’s app/account type and whose name is a non-empty string. It returns that property name, or raises a `PipedreamError` if none exists.

**Call relations**: `PipedreamBroker.execute` calls this just before running an action, because it needs to bind the selected connected account into the correct input field. It uses `_props` to read the action’s properties in a consistent way.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 252–275)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Turns Pipedream action properties into a JSON schema, which is a standard machine-readable description of allowed inputs. It hides Pipedream-only fields so the agent only sees values it should actually provide.

**Data flow**: It receives a list of property dictionaries. For each usable property, it reads the name, type, description or label, converts the Pipedream type into a JSON-schema type, and marks it required unless Pipedream says it is optional. It returns an object schema with properties and, when needed, a required list.

**Call relations**: `PipedreamBroker.schema` calls this after `_props` extracts the raw action properties. `_str` helps it safely interpret type values, and the result becomes the input schema inside the returned `BrokerTool`.

*Call graph*: calls 1 internal fn (_str); called by 1 (schema).


##### `_str`  (lines 278–279)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into text only if it is already a string. It prevents non-string data from leaking into descriptions, types, or other text fields.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: This small helper is used by `PipedreamBroker.schema`, `_input_schema`, and `_listed_tools` wherever Pipedream metadata might contain missing or unexpected values. It keeps those paths simple and avoids repeated type checks.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### Web research tools
Research tools give agents a provider-neutral interface for web search, page fetching, and specialized research categories.

### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `request handling during an agent turn`

This file is the bridge between an agent asking to research something and the search backend that can actually do it. Without it, the agent would not have a clean, safe way to search the web, read public URLs, or ask for specialized results such as videos or shopping listings.

The file first describes the shape of each tool’s input using Pydantic models, which are data checkers that reject badly shaped arguments before work begins. For example, web search accepts up to five short queries, while URL fetching requires an HTTP or HTTPS address.

When a tool runs, it looks up the search provider attached to the current tool context. That provider lives on the host side, so secrets like API keys are not exposed to the sandbox. Search results are turned into simple JSON text containing URLs, titles, snippets, dates, highlights, and sometimes an answer.

The fetch tool has an important safety note. Some providers can search but cannot fetch pages; in that case the tool returns a clear error telling the agent to use another route. When fetching is supported, the returned page is labeled with crawler provenance: the page was fetched by the provider’s crawler session, not by the user’s workspace. Like borrowing someone else’s library card, any login or identity seen in the result belongs to the crawler, not the member.

#### Function details

##### `_provider`  (lines 116–119)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This small helper retrieves the search provider for the current tool call. It makes sure the rest of the file does not silently continue when no search backend has been configured.

**Data flow**: It receives the tool context, which may or may not contain a search provider. If a provider is present, it returns it. If none is present, it raises an error saying that no search provider is configured for this turn.

**Call relations**: The three tool handlers call this first, before doing any search or fetch work. It acts like a checkpoint at the door: _search_web, _fetch_url, and _search_vertical all depend on it to either hand them a usable provider or stop the request loudly.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 122–136)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This helper converts search hits into a plain JSON string that the model can read. It gives all search-style tools the same output shape, so callers do not need to learn a different format for each search mode.

**Data flow**: It receives a list of search hits and an optional answer from the provider. It copies the useful fields from each hit, such as URL, title, snippet text, publish date, and highlights, then adds the optional answer if one exists. It returns one JSON-formatted text string.

**Call relations**: _search_web and _search_vertical call this after they receive results from the provider. This helper then hands off to json.dumps to do the final conversion from Python data into JSON text.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 139–154)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the general web search tool. It lets the agent run several short web searches in one call, then combines the results into one response.

**Data flow**: It receives the tool context and checked search arguments, including the queries, optional recency filter, and optional allowed domains. It gets the current provider, sends one search request per query, asks for a default number of results each time, and collects all returned hits. It keeps the first provider-supplied answer it sees, converts everything to JSON text, and returns that text inside a tool result.

**Call relations**: When the search_web tool is invoked, this function drives the whole flow. It calls _provider to get the backend, builds SearchQuery objects for each query, asks the provider to search, uses _results_json to format the combined response, and wraps the final text in TextContent and ToolResult for the tool system.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


##### `_fetch_url`  (lines 157–176)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the URL fetching tool. It reads a public web page through the configured search provider’s crawler, optionally asking the provider to extract or summarize specific information.

**Data flow**: It receives the tool context and checked fetch arguments, including the URL, optional prompt, maximum length, and cache-bypass flag. It gets the provider, first checks whether that provider supports fetching, and returns an error message if not. If fetching is supported, it builds a fetch request, waits for the page, adds the page URL, text, optional summary, and a provenance warning, then returns the JSON text in a tool result.

**Call relations**: When fetch_url is invoked, this function is responsible for deciding whether fetching is possible at all. It calls _provider, may stop early with a ToolResult marked as an error, or otherwise creates a FetchRequest for the provider. It uses json.dumps directly because fetched pages have a different response shape from search results.

*Call graph*: calls 1 internal fn (_provider); 4 external calls (__init__, __init__, __init__, dumps).


##### `_search_vertical`  (lines 179–186)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind specialized searches, such as images, people, academic papers, videos, or shopping results. It tells the provider what kind of content the agent wants instead of doing a general web search.

**Data flow**: It receives the tool context and checked vertical-search arguments: a content type and a short query. It gets the provider, builds a search request that includes the selected vertical, asks for the default number of results, converts the provider’s hits and optional answer into JSON, and returns that text in a tool result.

**Call relations**: When search_vertical is invoked, this function follows a shorter version of the web search path. It calls _provider to get the backend, creates one SearchQuery with the vertical included, sends it to the provider, then hands the results to _results_json before wrapping them in TextContent and ToolResult.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


### Business workspace tools
Slack and YC extensions provide user-facing business-tool workflows for workspace setup, search, questions, skills, and authorization.

### `extensions/slack/ufo_ext_slack/tools.py`

`domain_logic` · `Slack setup and Slack tool use`

Slack cannot be used safely just because someone types a channel name in chat. The system needs a bot token, a signing secret, proof of which Slack workspace the bot belongs to, and proof that Slack can really reach this deploy. This file turns that setup into guided tools the agent can call during conversation.

There are two install routes. The preferred route is OAuth, which means the workspace owner gets an “Add to Slack” link and Slack sends the credentials back through a protected callback. The fallback route is a manifest, where the user creates their own Slack app from ready-made YAML and privately supplies the bot token and signing secret. Both routes end in the same place: a stored Slack identity and credentials tied to the UFO workspace.

The file also checks whether Slack has actually contacted this deploy with a valid signature. That matters because a token alone does not prove the public URL and signing secret are working. Think of it like setting up a doorbell: having the key is not enough; someone must ring it successfully.

Finally, the runtime tool `slack_channels` lets the agent search channels and direct messages by names, topics, purposes, or people, so it can find where to act instead of requiring exact Slack IDs.

#### Function details

##### `_events_url`  (lines 127–128)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to this deploy. This keeps Slack event URLs consistent in both setup paths.

**Data flow**: It takes the deploy’s public base URL, removes any trailing slash, then adds `/surface/slack`. The result is a complete Slack events endpoint URL.

**Call relations**: When Slack setup or manifest generation needs to tell Slack where to send messages and events, `slack_connect_handler` and `slack_manifest_handler` call this helper first.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 131–133)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a setup status into the standard tool response format. It gives the agent a small JSON report such as `not_configured`, `pending`, or `connected`, plus a human-readable hint.

**Data flow**: It receives a state name, a hint, an optional Slack events URL, and any extra fields. It turns them into JSON text, wraps that text in a tool content object, and returns it as a tool result.

**Call relations**: The setup flow uses this whenever it needs to report progress or a problem. `slack_connect_handler`, `_oauth_link`, and `_derive_manifest_identity` all hand their user-facing status messages through this function.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 136–181)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection workflow. Someone can call it repeatedly before, during, and after setup, and it will report the current state instead of blindly starting over.

**Data flow**: It reads the current workspace, public URL, stored credentials, and any saved Slack identity. If identity is missing, it either creates an OAuth install link or tries the manifest-based identity check. Once identity exists, it binds that Slack team to this UFO workspace, checks whether Slack has successfully reached the deploy, and returns a JSON status.

**Call relations**: This is the handler behind the `slack_connect` tool. It asks `_events_url` for the callback address, delegates OAuth setup to `_oauth_link`, delegates manifest setup to `_derive_manifest_identity`, checks final reachability with `_verified`, and uses `_state` to explain each outcome.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 184–214)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the one-click “Add to Slack” link for installs that use this deploy’s own Slack app. It also protects the install so only the workspace owner can start it.

**Data flow**: It checks whether the deploy has Slack app client credentials in environment variables. If not, it returns a message telling the user to use the manifest path. If credentials exist, it verifies the speaker is the workspace owner, creates a sealed credential handoff, builds the Slack authorization URL, and returns it in a status response.

**Call relations**: `slack_connect_handler` calls this when no Slack identity exists and the requested method is OAuth. This helper prepares the link, while Slack’s OAuth callback later completes the actual credential storage.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_owner, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 217–251)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path. It checks that the required private secrets have been supplied, then proves the bot token by resolving the Slack team and bot identity.

**Data flow**: It looks for the bot token and signing secret in private credential slots. If either is missing, it returns a setup message naming what still needs to be collected. If both are present, it confirms the speaker is the workspace owner, asks Slack identity resolution to validate the bot token, and returns either the identity or a helpful error diagnosis.

**Call relations**: `slack_connect_handler` calls this when the user chooses the manifest method. It uses `_state` for setup messages and `_token_diagnosis` when Slack rejects the token in a known way.

*Call graph*: calls 3 internal fn (speaker_is_owner, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 254–273)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has already contacted this deploy using the currently stored signing secret. This is the difference between “we know the bot identity” and “Slack can really deliver events here.”

**Data flow**: It builds the blob-storage key for the workspace’s Slack verification marker, reads that marker if it exists, reads the stored signing secret, and compares the marker’s fingerprint with the current secret’s fingerprint. It returns `true` only when the marker is present, readable, and matches the current secret.

**Call relations**: After `slack_connect_handler` has a Slack identity, it calls this helper to decide whether to report `pending` or `connected`. If the signing secret changes, this check naturally fails until Slack verifies the new setup.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, signing_secret_fingerprint, url_verified_blob_key).


##### `slack_manifest_handler`  (lines 276–291)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for users who create their own Slack app. The manifest includes the needed permissions, event subscriptions, bot name, and request URLs.

**Data flow**: It receives a desired bot display name and checks that it is simple and short enough for the template. It reads the deploy’s public base URL, builds the Slack events URL, fills the manifest template, and returns the YAML text as a tool result.

**Call relations**: This is the handler behind the `slack_app_manifest` tool. It uses `_events_url` so the generated Slack app points back to the same event endpoint used by the rest of the Slack surface.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 294–317)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches Slack conversations so the agent can find a channel, group direct message, or direct message without already knowing its Slack ID. This is useful when a user says something like “post it in #support” or “message Alex.”

**Data flow**: It reads the stored Slack bot token, loads the saved Slack identity, and refuses to continue if Slack is not connected yet. It then runs a Slack conversation search using the query text, converts the found conversations into JSON, and returns the result as untrusted text because the names and topics came from Slack users.

**Call relations**: This is the handler behind the `slack_channels` tool. It relies on the identity established by `slack_connect_handler`, reads identity through the Slack surface helper, and delegates the actual Slack-side scanning to `SlackConversationSearch`.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 320–326)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack token error codes into plain setup advice. It helps the user understand whether they probably copied the wrong token or whether Slack returned some other failure.

**Data flow**: It receives an error string from Slack. If the error is one of the common rejected-token cases, it returns a message telling the user to re-copy the Bot User OAuth Token; otherwise it returns a more general `auth.test` failure message.

**Call relations**: `_derive_manifest_identity` calls this when Slack identity resolution fails for the manifest path. The result becomes the hint shown through `_state`.

*Call graph*: called by 1 (_derive_manifest_identity).


### `extensions/yc/ufo_ext_yc/cli.py`

`io_transport` · `tool request handling`

This file sits at the boundary between UFO and YC services. Its job is to make sure YC access happens in a controlled way: first by getting and storing OAuth credentials, then by running the real `yc` command-line program with those credentials in a temporary private home folder. OAuth is a standard login flow where a service gives an app short-lived access tokens instead of a password.

The authentication half starts a “device authorization” flow. That means UFO asks YC for a code and a verification web page, the user visits that page, and then UFO later checks whether YC has approved the login. Pending login state is sealed through UFO’s credential system, so sensitive tokens are not left in plain extension storage. The file also remembers enough information to avoid restarting the same authorization unnecessarily.

The command-running half creates a throwaway directory, writes the saved YC credentials into the place where the YC CLI expects them, runs the requested command, reads its output with size limits, and deletes the directory afterward. This is like giving the CLI a temporary hotel room with only the key it needs, then cleaning the room when it leaves. If the CLI refreshes the credentials while it runs, this file tries to save the refreshed version back safely.

Without this file, YC tools would either be unable to log in and run, or they would have to handle secrets, subprocesses, timeouts, and oversized output in less consistent and less safe ways.

#### Function details

##### `YcDeviceAuthorization.validate_verification_url`  (lines 59–63)

```
def validate_verification_url(self) -> 'YcDeviceAuthorization'
```

**Purpose**: Checks that the login link returned by YC really points to YC’s account site. This prevents UFO from accidentally showing a user a verification link controlled by some other host.

**Data flow**: It starts with a parsed device authorization response from YC. It chooses the complete verification URL if one exists, otherwise the base verification URL, then checks the host name. If the host is `account.ycombinator.com`, the same authorization object continues forward; otherwise validation fails with an error.

**Call relations**: This validation runs when a YC device authorization response is turned into a `YcDeviceAuthorization` object during the start of login. It protects the later step where `YcAuth._start` shows the verification URL and user code to the person authorizing the account.


##### `YcRunner.run`  (lines 93–93)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: Defines the small contract for anything that can run YC CLI-style commands. It lets higher-level code ask for a YC command to be run without caring whether the runner is the real CLI or a test double.

**Data flow**: It receives command arguments and a session label. An implementation is expected to run that command for that session and return the command’s text output.

**Call relations**: This is the shape that `YcRead` depends on. In normal use, `YcCli` fulfills this contract, while tests or other callers could provide another runner with the same method.


##### `YcAuth.run`  (lines 101–116)

```
async def run(self, action: Literal['start', 'complete'], session: str) -> YcAuthResult
```

**Purpose**: Acts as the front door for YC authorization actions. It checks that the request is allowed and then sends the work to either the start or complete step.

**Data flow**: It receives an action, either `start` or `complete`, plus a session label. Before doing anything sensitive, it reads the tool context to confirm the YC extension exists, the speaker is authorizing their own private audience, credential storage is configured, the credential slot is declared, and the speaker owns the workspace. If those checks pass, it returns the result from the matching authorization step.

**Call relations**: The public `yc_auth` tool calls this after creating an HTTP client. `YcAuth.run` is the gatekeeper: once the request is proven safe, it calls `YcAuth._start` for a new device-code login or `YcAuth._complete` to finish a login that was already started.

*Call graph*: calls 2 internal fn (_complete, _start).


##### `YcAuth._start`  (lines 118–176)

```
async def _start(self, session: str) -> YcAuthResult
```

**Purpose**: Starts the YC device login flow and returns the web link and code the user must use. It also avoids creating duplicate pending authorizations when the same request is retried.

**Data flow**: It reads the extension store to see whether a pending authorization already exists. If the same idempotency key is found, it reopens the sealed pending data and returns the existing verification link and user code, unless credentials have already changed, in which case it reports that the account is connected. If no reusable pending login exists, it checks the current credential digest, calls YC’s device authorization endpoint, validates and bounds the response, seals the pending device code through the credential system, stores a small reference record, and returns an `authorization_required` result with the URL and code.

**Call relations**: This is called by `YcAuth.run` when the user asks to start authorization. It relies on `YcAuth._credential_digest` to notice whether credentials changed, `YcAuth._headers` to identify the CLI/session to YC, and `YcAuth._bound` to reject unexpectedly large YC responses before parsing them.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, time).


##### `YcAuth._complete`  (lines 178–230)

```
async def _complete(self, session: str) -> YcAuthResult
```

**Purpose**: Checks whether the user has finished the YC login page and, if so, stores the real credentials. If the user has not approved yet, it tells the caller that authorization is still pending.

**Data flow**: It reads the saved pending authorization from extension storage. If none exists, it checks whether valid credentials are already stored and either reports `connected` or raises an error telling the user to start first. If a pending authorization exists, it reopens the sealed device code, checks that it has not expired, then asks YC’s token endpoint for credentials. A successful response is validated and saved through the credential authorization system, and the pending record is removed. A pending or slow-down response returns `pending`; expired or failed responses clear the pending record and raise a clear error.

**Call relations**: This is called by `YcAuth.run` when the user asks to complete authorization. It uses the same helpers as the start step: `YcAuth._credential_digest` to detect if some other credential update already connected the account, `YcAuth._headers` for request identity, and `YcAuth._bound` to keep YC responses within safe size limits.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 4 external calls (__init__, __init__, loads, time).


##### `YcAuth._credential_digest`  (lines 232–240)

```
async def _credential_digest(self) -> str | None
```

**Purpose**: Creates a fingerprint of the currently stored YC credentials, if any. The fingerprint lets the auth flow tell whether credentials changed without comparing or storing the secret itself in ordinary extension state.

**Data flow**: It reads the YC credential slot from the extension credential store. If the slot has not been set, it returns nothing. If credentials exist, it validates that they have the expected shape, hashes the raw credential JSON with SHA-256, and returns the hash string.

**Call relations**: `YcAuth._start` and `YcAuth._complete` call this before deciding what to do with pending authorization state. It helps them distinguish “still waiting for login” from “credentials were already updated elsewhere.”

*Call graph*: called by 2 (_complete, _start); 1 external calls (sha256).


##### `YcAuth._headers`  (lines 242–247)

```
def _headers(self, session: str) -> dict[str, str]
```

**Purpose**: Builds the standard HTTP headers sent to YC’s authorization service. These headers identify the YC CLI version and the UFO conversation session making the request.

**Data flow**: It receives a session label. It returns a small dictionary containing a user agent, a YC CLI version header, and a session header.

**Call relations**: `YcAuth._start` uses these headers when asking YC for a device code, and `YcAuth._complete` uses them when exchanging that device code for credentials.

*Call graph*: called by 2 (_complete, _start).


##### `YcAuth._bound`  (lines 249–251)

```
def _bound(self, response: httpx.Response) -> None
```

**Purpose**: Rejects YC authentication responses that are larger than expected. This protects the process from reading or parsing an unexpectedly huge response body.

**Data flow**: It receives an HTTP response object and checks the byte length of its content. If the content is within the configured limit, nothing changes. If it is too large, it raises a `YcCliError`.

**Call relations**: `YcAuth._start` calls this before parsing the device authorization response, and `YcAuth._complete` calls it before parsing token or error responses. It is a safety check shared by both network steps.

*Call graph*: called by 2 (_complete, _start); 1 external calls (__init__).


##### `_read_bounded`  (lines 254–262)

```
async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes
```

**Purpose**: Reads output from a running YC CLI process while enforcing a maximum size. This prevents a command from filling memory with unlimited output or error text.

**Data flow**: It receives an asynchronous stream, such as standard output or standard error from a subprocess, and a byte limit. It reads the stream in chunks, adds each chunk to a list, and keeps a running total. If the total grows past the limit, it raises an error; otherwise it returns all collected bytes when the stream ends.

**Call relations**: `YcCli._execute` starts this helper twice, once for normal output and once for error output. The helper gives `_execute` safe, bounded byte strings to decode or include in an error message.

*Call graph*: called by 1 (_execute); 2 external calls (__init__, read).


##### `YcCli.run`  (lines 270–280)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: Runs a real YC CLI command using stored UFO credentials, then cleans up afterward. It is the main method that turns a requested YC command into returned text.

**Data flow**: It reads the saved YC credentials, validates them, and prepares a temporary private home directory containing those credentials. It then runs the command in that temporary environment. Whether the command succeeds or fails, it tries to persist any refreshed credentials written by the CLI, and finally deletes the temporary directory.

**Call relations**: `YcRead.run` uses a `YcRunner`, and in normal tool use `yc_read` supplies `YcCli` as that runner. Inside this method, `YcCli.run` delegates setup to `YcCli._prepare_home`, command execution to `YcCli._execute`, and credential refresh reconciliation to `YcCli._persist_refresh`.

*Call graph*: calls 2 internal fn (_execute, _persist_refresh); 1 external calls (to_thread).


##### `YcCli._prepare_home`  (lines 282–289)

```
def _prepare_home(self, raw: str) -> Path
```

**Purpose**: Creates a temporary private home folder that makes the external YC CLI believe it is running as a normal user with YC credentials installed. This keeps credentials isolated from the real host environment.

**Data flow**: It receives the raw credential JSON string. It creates a temporary directory, locks down its permissions, creates the `.yc/credentials.json` path inside it, writes the credential text there, restricts the credential file’s permissions, and returns the temporary home path.

**Call relations**: `YcCli.run` calls this before launching the CLI. The returned folder is later passed to `YcCli._execute` as the CLI’s `HOME` environment value and is removed by `YcCli.run` after the command is done.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `YcCli._execute`  (lines 291–332)

```
async def _execute(self, home: Path, args: tuple[str, ...], session: str) -> str
```

**Purpose**: Launches the external `yc` program, waits for it, and returns its output safely. It also turns common failures, such as a missing executable, timeout, too much output, or nonzero exit status, into clear `YcCliError` messages.

**Data flow**: It receives the temporary home directory, command arguments, and session label. It builds a limited environment with `HOME`, `PATH`, and `YC_CLI_SESSION`, starts the subprocess, reads stdout and stderr with size limits, and waits up to the configured timeout. If the process exits successfully, it decodes and returns stdout as text. If the CLI is missing, hangs, writes too much, is cancelled, or exits with an error, it kills the process when needed and raises an error with the best available message.

**Call relations**: `YcCli.run` calls this after preparing the temporary home. It hands stream reading to `_read_bounded`, using one task for normal output and one for error output, so subprocess communication stays safe and bounded.

*Call graph*: calls 1 internal fn (_read_bounded); called by 1 (run); 5 external calls (__init__, create_subprocess_exec, create_task, gather, timeout).


##### `YcCli._persist_refresh`  (lines 334–353)

```
async def _persist_refresh(self, home: Path, expected: str) -> None
```

**Purpose**: Saves refreshed YC credentials back into UFO’s credential store if the external CLI updated them while running. This matters because OAuth credentials can be renewed, and losing the renewal could make the next YC command fail.

**Data flow**: It reads the credentials file from the temporary home and validates it. If the file is unchanged, it does nothing. If it changed, it first tries to replace the old stored credentials with the refreshed ones. If another request updated credentials at the same time, it compares creation times and keeps retrying when the refreshed version is newer. If it cannot reconcile the updates safely, it raises an error.

**Call relations**: `YcCli.run` calls this in a cleanup step after `YcCli._execute`, even when command execution is finishing. It uses the credential store’s rotation operation so refreshes are saved only when they match the expected previous value or can be safely reconciled.

*Call graph*: called by 1 (run); 2 external calls (__init__, to_thread).


##### `YcReadInput.validate_action`  (lines 366–373)

```
def validate_action(self) -> 'YcReadInput'
```

**Purpose**: Checks that a requested YC read action has the fields it needs and does not include fields that only make sense for another action. This catches bad tool input before it becomes a confusing CLI command.

**Data flow**: It receives a parsed YC read input object. For `ask` and `search`, it requires a query. For `skills_read`, it requires a skill name. It also rejects `entity` unless the action is `search`. If the input is consistent, the same object continues; otherwise validation fails with a clear message.

**Call relations**: This validation runs when tool input is parsed into `YcReadInput`. It prepares clean, predictable input for `YcRead.run`, which then translates the action into actual YC CLI arguments.


##### `YcRead.run`  (lines 380–395)

```
async def run(self, args: YcReadInput, session: str) -> str
```

**Purpose**: Translates a high-level YC read request into the exact YC CLI command that should be run. It is the small adapter between tool-friendly actions like “search” and command-line arguments like `search ... --json`.

**Data flow**: It receives a validated `YcReadInput` object and a session label. Based on the action, it builds a tuple of command arguments: agent ask, search, skills list, skill read, or tools context. It then passes that command and session to its runner and returns the runner’s text output.

**Call relations**: `yc_read` creates a `YcRead` with a `YcCli` runner, then calls this method. `YcRead.run` does not know the details of credentials or subprocesses; it hands the finished command to the runner, which is normally `YcCli.run`.


##### `yc_read`  (lines 398–404)

```
async def yc_read(ctx: ToolContext, args: YcReadInput) -> ToolResult
```

**Purpose**: Is the tool entry function for read-only YC operations. It lets the larger UFO tool system call YC search, ask, skills, or context commands and receive the result as tool text.

**Data flow**: It receives the tool context and parsed YC read arguments. It checks that the YC extension context exists, builds a conversation-based session label, creates a real `YcCli` backed by the extension credentials, runs the requested YC read operation, and wraps the returned text in a `ToolResult`.

**Call relations**: The UFO tool runtime calls this when a user invokes the YC read tool. This function wires together `YcRead` and `YcCli`: `YcRead` decides which command to run, and `YcCli` runs it with authenticated credentials.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `yc_auth`  (lines 407–414)

```
async def yc_auth(ctx: ToolContext, args: YcAuthInput) -> ToolResult
```

**Purpose**: Is the tool entry function for connecting a YC account. It starts or completes the device-code login flow and returns a small JSON status message to the caller.

**Data flow**: It receives the tool context and an auth action. It checks that the YC extension context exists, opens an HTTP client with a timeout, builds a conversation-based session label, runs `YcAuth`, converts the auth result to JSON without empty fields, and wraps that JSON text in a `ToolResult`.

**Call relations**: The UFO tool runtime calls this when a user invokes the YC auth tool. This function creates the network client and hands the real authorization work to `YcAuth.run`, which then dispatches to the start or complete authorization step.

*Call graph*: 4 external calls (__init__, __init__, __init__, AsyncClient).

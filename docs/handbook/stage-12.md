# External connectors, credentials, and egress mediation  `stage-12`

This stage is the system’s controlled doorway to the outside world. It is used both during setup, when a user connects accounts, and during the main work loop, when agents call tools or send requests. Its job is to make outside services usable without spreading private keys and tokens through the system.

Connection setup and credential vaulting is the lockbox. It records which workspace owns each connection, guides OAuth sign-in or API-key setup, and stores secrets safely. Connector action execution and proxy access is the guard at runtime. It checks whether an outbound request is allowed, adds the right secret only when permitted, and lets proxies report usage.

The shared connector files provide the common doorway for services like Gmail, GitHub, Composio, and Pipedream. The Composio client talks to Composio to create login links, list tools, run actions, and prepare uploads while keeping real third-party tokens outside UFO. iMessage support chooses between Spectrum Cloud, a fake local development line, or no iMessage setup. Slack hooks add service-specific finishing touches around sends and account connection events without blocking the main flow.

## Sub-stages

- [Connection setup and credential vaulting](stage-12.1.md) `stage-12.1` — 13 files
- [Connector action execution and proxy access](stage-12.2.md) `stage-12.2` — 14 files

## Files in this stage

### External service front doors
Entry clients broker access to hosted third-party connector services and choose the right iMessage provider.

### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling`

Composio acts like a hotel front desk for hundreds of outside services: the agent asks the front desk what services exist, sends the user there to approve access, and later asks it to run a specific tool. This file contains the client that talks to that front desk over Composio's web API. It is careful about trust. Before accepting a connected account, it checks that the account belongs to the expected workspace user, is active, and matches the requested connector. Before advertising a connector, it checks that Composio can actually authenticate it, that it has tools, and that it is not in a hand-maintained banned list of providers known to be unusable here. The file also turns Composio's raw tool information into safer shapes for the agent. For example, when a tool can accept a file, the schema shown to the agent asks for a workspace file path, not Composio's internal storage details. Searches use Composio's Tool Router, a remote helper that suggests matching tools and plans. Tool execution, however, goes through Composio's execute API so Composio keeps and injects the real account token itself. Without this file, the project could not safely connect user accounts, discover Composio tools, or run them through the broker.

#### Function details

##### `connectable`  (lines 110–134)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether this deployment should offer a Composio toolkit to users. It rejects toolkits that are known to be unsuitable, have no Composio-managed authentication path, or have no tools to run.

**Data flow**: It receives a toolkit slug and a catalog record from Composio. It checks the slug against the banned list, then reads the record for managed authentication schemes and a tool count. It returns true only when all of those facts show the toolkit can realistically be connected and used.

**Call relations**: The toolkit lookup and catalog listing paths call this before showing or claiming a provider. That means unusable connectors are filtered before a user tries to connect them, instead of producing a dead grant later.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 141–144)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Builds a clear exception for failed or unusable Composio responses. It keeps both the status code and the response body so callers can report what went wrong.

**Data flow**: It receives an HTTP-style status number and a text body. It turns them into a readable error message and stores the original pieces on the error object. The output is an exception ready to be raised.

**Call relations**: Higher-level client methods use this when Composio returns a bad response or a response missing required fields. The response parser also uses it so malformed API replies fail loudly.

*Call graph*: called by 6 (_account, _auth_config, connect_link, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 161–170)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the hosted consent link a user opens to connect an outside account through Composio. This is the start of the OAuth flow, where OAuth means the common web login-and-consent process for granting app access.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates an authentication configuration for that toolkit, then posts those details to Composio. It returns the redirect URL the user should visit, or raises an error if Composio did not provide one.

**Call relations**: This method relies on _auth_config to choose the right authentication setup and _post to send the request. If the reply is missing the link, it raises ComposioError rather than letting the connection flow continue broken.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 172–176)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a Composio connected account is usable and then wraps it as an OAuthAccount for the rest of the connector system. It is the safety check after a user has granted access.

**Data flow**: It receives the connected account id plus the expected user and toolkit. It asks _account to verify ownership, active status, and toolkit match. If that passes, it returns an OAuthAccount containing the account id, not a secret token.

**Call relations**: This is the public confirmation step, while _account does the detailed inspection. Downstream code receives only the approved account id because Composio keeps the real token.

*Call graph*: calls 1 internal fn (_account); 1 external calls (__init__).


##### `ComposioClient._account`  (lines 178–207)

```
async def _account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> dict[str, object]
```

**Purpose**: Fetches and validates the raw Composio account record. It prevents using an account that belongs to another workspace user, is inactive, or authenticates the wrong connector.

**Data flow**: It receives an account id and the user and toolkit it should belong to. It fetches the account from Composio, compares the owner, checks that the status is ACTIVE, and reads the toolkit slug. It returns the raw account payload when all checks pass, or raises a clear error when they do not.

**Call relations**: connected_account calls this before accepting a grant. It uses _get for the API call, raises ComposioError for ownership or toolkit mismatches, and raises GrantUnusable when the user needs to reconnect an inactive account.

*Call graph*: calls 3 internal fn (__init__, _get, __init__); called by 1 (connected_account).


##### `ComposioClient.account_label`  (lines 209–212)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a friendly label for a connected account, such as an alias set in Composio. This can be used to show a human-readable account name instead of only an id.

**Data flow**: It receives a connected account id and fetches that account record. It reads the alias field. It returns the alias if it is a non-empty string, otherwise it returns nothing.

**Call relations**: This is a small read-only helper built on _get. It does not validate the account like connected_account does; it only asks Composio for display text.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 214–244)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the tools Composio offers for a given toolkit, optionally narrowed by a search query. It follows Composio's pages so tools beyond the first page are not invisible.

**Data flow**: It receives a toolkit slug and optional query text. It repeatedly asks Composio for pages of tools, collecting dictionary-shaped tool rows until there are no more pages or the project limit is reached. It returns a tuple of tool records, capped at the maximum this project will display.

**Call relations**: The broker uses this when it needs to resolve or investigate a tool slug miss. Internally it uses _get for each page and stops early when Composio indicates the listing is complete.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 246–247)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full schema for one Composio tool. A schema is the tool's instruction sheet: what it does and what inputs it expects.

**Data flow**: It receives a tool slug. It sends a GET request for that specific tool and returns Composio's dictionary response. It does not reshape the response here.

**Call relations**: This is a direct wrapper around _get for callers that need details about one known tool. Other code can then decide how to present or use that schema.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 249–266)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a single toolkit slug is safe and usable for this deployment, and returns its display name if so. It also blocks suspicious slugs before they can become part of a URL path.

**Data flow**: It receives a user-supplied slug. It first requires the slug to match a simple safe character set, then fetches the toolkit from Composio. If the toolkit is missing or fails the connectable checks, it returns nothing; otherwise it returns Composio's name or the slug as a fallback.

**Call relations**: This method combines _get with the connectable policy gate. It is used when the resolver wants to know whether this extension can claim a requested provider.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 268–295)

```
async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads a page of Composio toolkits that this project can offer to users. It filters the catalog so users see only connectable providers.

**Data flow**: It receives search text, a page size, and an optional cursor that means where to continue from. It fetches a toolkit page from Composio, skips malformed or non-connectable items, and turns the rest into CatalogEntry objects. It returns a CatalogPage containing entries and the next cursor if there is another page.

**Call relations**: This is the catalog browsing path. It uses _get to talk to Composio, connectable to apply local policy, and the catalog data models to hand a clean page back to the connector system.

*Call graph*: calls 2 internal fn (_get, connectable); 2 external calls (__init__, __init__).


##### `ComposioClient.execute_tool`  (lines 297–311)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a named Composio tool for a broker user, optionally tied to a specific connected account. It sends the request to Composio, where the real third-party token is held and applied.

**Data flow**: It receives a tool slug, input arguments, a user id, and optional connected account and idempotency key. It builds the request body, checks that the JSON payload is not too large, adds the idempotency header if supplied, and posts to Composio. It returns Composio's response dictionary.

**Call relations**: Tool execution callers use this after discovery and grant selection. It relies on _post for transport and uses JSON encoding to enforce the payload size limit before any network call.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 313–337)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file that will be passed to a tool. It returns both the storage key the tool should mention and, when needed, the temporary upload URL where bytes should be sent.

**Data flow**: It receives toolkit and tool slugs plus filename, MIME type, and MD5 checksum. It posts those facts to Composio's upload request endpoint. It returns a ComposioUpload with a key and possibly a PUT URL, or raises an error if the response lacks a usable key or URL.

**Call relations**: This prepares file arguments before a tool is executed. It uses _post for the API call and ComposioError when Composio's upload response cannot safely be used.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 339–350)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The Tool Router is a remote search helper that can suggest tools based on what the user wants to do.

**Data flow**: It receives a broker user id and a list of toolkit slugs to enable. It posts those to Composio and expects a session id plus an MCP URL, where MCP is the protocol endpoint used to call the search tool. It returns a ToolRouterSession or raises an error if the response is incomplete.

**Call relations**: search_connector_tools calls this when no cached session exists for a user and connector. The returned URL is then used for the actual semantic search call.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 352–370)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration to use for a toolkit, creating a Composio-managed one if none exists. This decides what kind of consent flow the user will see.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts its id if present. If none exists, it posts a request to create a managed auth config, then returns the new id or raises an error if no id was returned.

**Call relations**: connect_link calls this before creating a consent link. It uses _get, _post, and _auth_config_id so existing operator-created configurations take priority over creating a new managed one.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 372–374)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends one GET request to Composio and returns a checked dictionary response. GET requests are used for reading records, catalogs, and schemas.

**Data flow**: It receives a path and optional query parameters. It opens an HTTP client, sends the request, and passes the response to _body for error checking and JSON parsing. It returns the parsed dictionary.

**Call relations**: Most read operations in this client go through _get. It gets the configured HTTP client from _http and delegates response validation to _body.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_account, _auth_config, account_label, connectable_toolkit, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 376–380)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends one POST request to Composio and returns a checked dictionary response. POST requests are used for creating links, sessions, uploads, auth configs, and tool executions.

**Data flow**: It receives a path, a JSON body, and optional extra headers. It opens an HTTP client, sends the request, and gives the response to _body. It returns the parsed dictionary response.

**Call relations**: All write or action-style API calls in this file use _post. Like _get, it centralizes transport setup through _http and response handling through _body.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 382–388)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Creates the temporary HTTP client used for a single Composio request. It attaches the base URL, API key, timeout, and optional test transport.

**Data flow**: It reads the ComposioClient's API key and optional transport override. It builds an httpx asynchronous client configured for Composio's API. The caller uses that client to send a request and then closes it through an async context manager.

**Call relations**: _get and _post call this every time they make a request. This keeps connection use simple and lets tests swap in a fake transport cleanly.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 391–399)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a safe Python dictionary. It rejects failed responses and unexpected response shapes.

**Data flow**: It receives an HTTP response. If the status code signals failure, it raises ComposioError with the response text. If the body is empty, it returns an empty dictionary; otherwise it parses JSON and requires the result to be an object-like dictionary.

**Call relations**: _get and _post use this after every Composio request. It is the shared guardrail that prevents callers from quietly accepting errors or non-object API replies.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 402–428)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the simpler file language used by this project. Instead of exposing Composio's internal file store fields, it asks the agent for a workspace file path.

**Data flow**: It receives any schema value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object requiring the workspace file key. For nested dictionaries and lists, it walks through and rewrites their contents; other values pass through unchanged.

**Call relations**: _search_result uses this when turning Tool Router results into broker tools. This keeps the model-facing schema aligned with how the project stages files.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 431–438)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first usable authentication configuration id from a Composio list response. It is a small helper for the auth setup flow.

**Data flow**: It receives a response dictionary. It looks for an items list and scans for the first dictionary item with a string id. It returns that id, or nothing if the response does not contain one.

**Call relations**: _auth_config calls this after asking Composio for existing configurations. A found id lets the client reuse an existing setup instead of creating a new one.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 441–448)

```
def composio_client() -> ComposioClient
```

**Purpose**: Builds the default ComposioClient from the deployment's environment variable. It fails immediately if the API key is missing.

**Data flow**: It reads COMPOSIO_API_KEY from the process environment. If the value is present, it returns a ComposioClient using that key. If not, it raises an error explaining that Composio OAuth brokering cannot work without it.

**Call relations**: Other parts of the extension can call this when they need the real deployed client. It is the bridge from configuration in the environment to the client object used for requests.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 455–456)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. It avoids repeated type checks in Tool Router result parsing.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: _search_result uses this while reading nested Tool Router data that may be missing or malformed. It helps the parser skip bad shapes without crashing on simple type errors.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 459–462)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It is used for lists of tool slugs, plan steps, and warnings.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only non-empty strings and returns them in order.

**Call relations**: _search_result calls this whenever it reads list-like fields from Tool Router results. This keeps only text that can be shown or used safely.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 465–499)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router's raw search answer into the BrokerSearch format used by the dynamic connector tools. It gathers matching tools, suggested plan steps, guidance, and known pitfalls.

**Data flow**: It receives the raw result dictionary from the remote search call. It reads nested data, collects primary and related tool slugs without duplicates, attaches each tool's description and rewritten input schema, and gathers planning and warning text. It returns a BrokerSearch object ready for the agent-facing layer.

**Call relations**: search_connector_tools calls this after the remote MCP search finishes. It uses _dict, _str_tuple, and workspace_file_schema to clean and reshape Composio's response before creating BrokerTool and BrokerSearch objects.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 502–525)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Performs semantic search for tools within one connector, using Composio's Tool Router. Semantic search means the user can describe a goal in normal language and get likely matching tools plus advice.

**Data flow**: It receives a ComposioClient, workspace id, connector slug, and query text. It builds the Composio broker user id, reuses or creates a cached Tool Router session for that user and connector, then calls the remote search tool with the query. It converts the raw answer into BrokerSearch and returns it.

**Call relations**: This is the high-level search flow. It calls tool_router_session when a session is not already cached, uses mcp_session.mcp_call_tool to contact the Tool Router endpoint, and hands the response to _search_result for cleanup.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `provider setup, message streaming, send/receive request handling`

This file is the bridge between the app and Spectrum’s hosted iMessage service. Without it, the project could not sign in to Spectrum, receive iMessage events, send texts, upload or download attachments, or know whether iMessage is available at all.

The main object is `SpectrumProject`, which represents one Spectrum project using a project ID and secret. It first asks Spectrum Cloud over ordinary HTTPS for a short-lived shared line token. That token is like a temporary door badge: later calls use it to open a secure gRPC connection, which is a fast remote-procedure protocol for talking to Spectrum’s message and attachment services. The file caches the token until shortly before it expires, so normal message traffic does not need to re-authenticate every time.

The file also translates raw Spectrum events into the project’s own simpler message shape. It filters out messages that should not trigger app behavior, such as outgoing messages, spam, system messages, stickers, hidden attachments, or empty events.

At startup or provider selection time, helper functions check environment variables for Spectrum credentials. If credentials exist, they build a reusable `SpectrumProject`. If this is a plain local development setup, they use `LocalLine` instead. Otherwise they raise a clear “not configured” error.

#### Function details

##### `SpectrumProject.installation_id`  (lines 94–95)

```
def installation_id(self) -> str
```

**Purpose**: Returns a stable name for this Spectrum-backed iMessage installation. Other parts of the system can use it to identify this provider instance without exposing the secret.

**Data flow**: It reads the project ID stored on the `SpectrumProject` object, prefixes it with `project:`, and returns that combined string. It does not contact the network or change any state.

**Call relations**: This is a small identity hook on `SpectrumProject`. No in-file caller is shown, which suggests it is used through the wider message-provider interface when the system needs to label the active installation.


##### `SpectrumProject.line`  (lines 97–118)

```
async def line(self) -> SpectrumLine
```

**Purpose**: Gets the shared Spectrum iMessage line token needed before any message or attachment call can be made. It reuses a still-valid token, and only asks Spectrum Cloud for a new one when needed.

**Data flow**: It looks up the current event-loop-specific token state. If a cached `SpectrumLine` exists and will not expire soon, it returns it. Otherwise it sends an authenticated HTTPS request to Spectrum Cloud, checks that the response has the expected shape, stores the new token and expiry time, and returns a `SpectrumLine` containing the token.

**Call relations**: All live Spectrum operations call this first: catch-up, subscription, sending text, sending attachments, and downloading attachments. It relies on `_loop` to get safe per-event-loop state and `_request` to make the cloud HTTP call.

*Call graph*: calls 2 internal fn (_loop, _request); called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 120–136)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: Keeps the HTTP client, lock, and token cache safe for the currently running asyncio event loop. An asyncio event loop is the scheduler that runs asynchronous tasks.

**Data flow**: It reads the current running event loop and checks whether this `SpectrumProject` already has state for it. If yes, it returns that state. If not, it creates a `SpectrumLoop` with either the original shared client and lock or a new client and lock for an additional loop, stores it, and returns it.

**Call relations**: `line`, `_request`, and `invalidate` call this whenever they need token or HTTP state. It is the quiet bookkeeping layer that prevents async objects created for one loop from being incorrectly reused in another.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.assign_line`  (lines 138–168)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: Makes sure a given phone number is registered with Spectrum and returns the shared iMessage number assigned for talking to it. This matters because a shared line may only message a phone after the phone has opened the conversation.

**Data flow**: It asks Spectrum Cloud for the project’s users, validates the response, and searches for the requested phone number. If found, it returns that user’s assigned phone number. If not found, it sends a registration request with an idempotency key, validates the created user response, and returns the newly assigned phone number.

**Call relations**: No in-file caller is shown, but this belongs to the provider flow used when a phone number must be connected to the shared line. It depends on `_request` for HTTPS calls and raises `SpectrumCloudError` when Spectrum rejects or returns malformed data.

*Call graph*: calls 1 internal fn (_request); 2 external calls (__init__, TypeAdapter).


##### `SpectrumProject._request`  (lines 170–193)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: Sends authenticated HTTPS requests to Spectrum Cloud and turns failed HTTP responses into the file’s own clear error type.

**Data flow**: It takes an HTTP method, a path, optional JSON body, and optional idempotency key. It builds headers, adds Basic Authentication using the project ID and secret, sends the request to Spectrum Cloud, raises `SpectrumCloudError` for non-success HTTP status codes, and returns the decoded JSON response body.

**Call relations**: `line` uses this to fetch shared-line tokens, and `assign_line` uses it to list or create Spectrum users. It calls `_loop` so the request goes through the right async HTTP client for the current event loop.

*Call graph*: calls 1 internal fn (_loop); called by 2 (assign_line, line); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 195–196)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: Creates a secure gRPC channel to Spectrum’s iMessage service. A gRPC channel is the network connection used for structured remote calls such as sending a message or subscribing to events.

**Data flow**: It uses Spectrum’s iMessage server address and SSL credentials, then returns a secure asynchronous gRPC channel. It does not send a request by itself.

**Call relations**: The streaming and message methods call this when they are ready to talk to Spectrum’s gRPC services: catch-up, subscribe, send text, send attachment, and download attachment.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 198–201)

```
async def invalidate(self) -> None
```

**Purpose**: Forgets the cached line token so the next operation must fetch a fresh one. This is useful after authentication problems or when the token should no longer be trusted.

**Data flow**: It gets the current loop’s state, takes the async lock so no other task changes the cache at the same time, clears the token state dictionary, and returns nothing.

**Call relations**: No in-file caller is shown, but it fits the provider’s recovery path. It uses `_loop` for the correct token cache and prepares `line` to perform a fresh Spectrum Cloud token request next time.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 203–207)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: Recognizes one specific kind of remote error: a bad event cursor. A cursor is a saved position in an event stream, like a bookmark in a log.

**Data flow**: It receives an exception, checks whether it is a gRPC asynchronous RPC error, then checks whether Spectrum labeled it `INVALID_ARGUMENT`. It returns `true` only for that case.

**Call relations**: No in-file caller is shown, but this is likely used by higher-level event-reading code to decide when a saved stream position cannot be used and a reset or catch-up is needed.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 209–210)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: Tells the rest of the system whether an exception came from outside services or networking rather than from local program logic.

**Data flow**: It receives an exception and checks whether it is a gRPC error, a `SpectrumCloudError`, or an HTTP error. It returns a boolean and changes nothing.

**Call relations**: No in-file caller is shown, but it is part of the provider’s error-classification behavior. Higher-level retry or reporting code can use it to treat network and Spectrum failures differently from internal bugs.


##### `SpectrumProject.error_code`  (lines 212–215)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: Turns an exception into a short readable code for logs, metrics, or retry decisions.

**Data flow**: It receives an exception. If it is a gRPC error, it returns the gRPC status name, such as `INVALID_ARGUMENT`; otherwise it returns the exception class name.

**Call relations**: No in-file caller is shown, but it complements `external_error` and `invalid_cursor` by giving the orchestration layer a compact way to describe what went wrong.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 217–238)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Reads past Spectrum iMessage events from a saved point and yields them in the project’s common provider-event format. This lets the app recover messages it missed while offline or disconnected.

**Data flow**: It first gets a valid line token. It builds a catch-up request, optionally placing the caller’s `after_sequence` bookmark into it. Over a secure gRPC channel, it streams frames from Spectrum, converts completion frames into head-sequence events, converts message-change frames into inbound messages when appropriate, and yields `ProviderEvent` objects one by one.

**Call relations**: This method calls `line` for authentication, `channel` for the gRPC connection, `rpc_metadata` for authorization headers, and `_inbound_message` to cleanly translate raw Spectrum message events. It is the historical-event counterpart to `subscribe`, which watches new live events.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 240–256)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Listens for new live iMessage events from Spectrum and yields them as provider events. This is the always-on stream used while the app is running.

**Data flow**: It gets a valid line token, opens a secure gRPC subscription, marks the supplied `ready` event once the subscription has started, then loops over incoming frames. Each frame becomes a `ProviderEvent`, with a sequence number when present and an inbound message only when the raw event represents a usable received message.

**Call relations**: It uses `line`, `channel`, `rpc_metadata`, and `_inbound_message`, just like `catch_up`, but it follows the live message stream rather than a backlog. The `ready` signal lets caller code know it is safe to consider the subscription active.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 258–271)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: Sends a plain text iMessage through Spectrum to an existing conversation. It returns Spectrum’s message ID for the sent message.

**Data flow**: It receives a conversation ID, text, and idempotency key. It gets a valid line token, builds a Spectrum send-text request using the idempotency key as the client message ID, sends it over a secure gRPC channel with authorization metadata, and returns the GUID of the message Spectrum created.

**Call relations**: This send path calls `line` before doing anything, then uses `channel` and `rpc_metadata` to contact Spectrum’s message service. The idempotency key helps protect callers from accidentally sending duplicates if a retry happens.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 273–300)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: Sends a file attachment through Spectrum by first uploading the file and then sending a message that refers to that uploaded file.

**Data flow**: It receives the conversation ID, filename, raw bytes, and idempotency key. It gets a valid line token, opens a secure gRPC channel, uploads the attachment bytes to Spectrum with a separate upload idempotency key, then sends an attachment message pointing at the uploaded attachment GUID. It returns the GUID of the final message.

**Call relations**: This method combines two Spectrum services: the attachment service for upload and the message service for sending. It relies on `line`, `channel`, and `rpc_metadata`, and uses different idempotency metadata for upload versus final message send so retries stay safe.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 302–312)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: Downloads an attachment from Spectrum as a stream of byte chunks. Streaming avoids needing the whole file in memory at once.

**Data flow**: It receives an attachment ID, gets a valid line token, opens a secure gRPC channel, and starts a download request. As Spectrum sends frames, it yields only the primary file chunks as raw bytes.

**Call relations**: Callers use this when they need the actual contents of an attachment described on an inbound message. It uses `line`, `channel`, and `rpc_metadata` to authenticate and connect to Spectrum’s attachment service.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `_spectrum_pair`  (lines 321–326)

```
def _spectrum_pair() -> tuple[str, str] | None
```

**Purpose**: Reads the Spectrum project ID and project secret from deployment environment settings. It returns them only when both are present.

**Data flow**: It asks `deploy_env` for the configured project ID and secret. If either value is missing or empty, it returns `None`; otherwise it returns the two strings as a pair.

**Call relations**: `spectrum_configured` uses this to answer a simple yes-or-no question, and `spectrum_project` uses it to build the actual cloud provider. This is the central credential lookup for the file.

*Call graph*: called by 2 (spectrum_configured, spectrum_project); 1 external calls (deploy_env).


##### `spectrum_configured`  (lines 329–331)

```
def spectrum_configured() -> bool
```

**Purpose**: Reports whether this deployment has the credentials needed to use Spectrum Cloud for iMessage.

**Data flow**: It calls `_spectrum_pair` and returns `true` if that function found both required values, or `false` if not. It does not create a provider or contact Spectrum.

**Call relations**: `imessage_offered` uses this when deciding whether iMessage should appear available, and `line_provider` uses it when choosing between Spectrum, local development, and not configured.

*Call graph*: calls 1 internal fn (_spectrum_pair); called by 2 (imessage_offered, line_provider).


##### `imessage_offered`  (lines 334–337)

```
def imessage_offered(public_base_url: str | None) -> bool
```

**Purpose**: Decides whether the iMessage option should be offered for this deployment. It allows either a real Spectrum setup or a plain local development setup.

**Data flow**: It receives the public base URL, checks whether Spectrum credentials are configured, and if not checks whether the URL represents a plain local setup. It returns a boolean.

**Call relations**: This is a lightweight availability check. It calls `spectrum_configured` for production-style credentials and `plain_local` for the development fallback.

*Call graph*: calls 1 internal fn (spectrum_configured); 1 external calls (plain_local).


##### `line_provider`  (lines 340–347)

```
def line_provider(public_base_url: str | None) -> MessageProvider
```

**Purpose**: Chooses the actual iMessage provider object for this deployment. It returns Spectrum in real configured deployments, a local line in plain local development, or a clear configuration error otherwise.

**Data flow**: It receives the public base URL. If Spectrum credentials exist, it returns the cached `SpectrumProject`. If not, but the URL is plain local, it returns a new `LocalLine`. If neither condition is true, it raises `ProviderNotConfigured` with instructions for which variables to set.

**Call relations**: This is the selection point that other startup code would call when it needs an iMessage provider. It uses `spectrum_configured`, `spectrum_project`, and `plain_local` to pick the path.

*Call graph*: calls 2 internal fn (spectrum_configured, spectrum_project); 3 external calls (__init__, __init__, plain_local).


##### `spectrum_project`  (lines 351–362)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: Builds and caches the reusable `SpectrumProject` object for this process. Caching means the app does not create a new HTTP client and token cache every time it asks for the provider.

**Data flow**: It reads the credential pair through `_spectrum_pair`. If missing, it raises `ProviderNotConfigured`. If present, it creates a `SpectrumProject` with the ID, secret, an async HTTP client, an async lock, and an empty token cache, then returns it. Because it is cached, later calls return the same object.

**Call relations**: `line_provider` calls this when Spectrum is configured. The returned project then supplies all cloud-backed provider operations such as sending, subscribing, catching up, and attachment transfer.

*Call graph*: calls 1 internal fn (_spectrum_pair); called by 1 (line_provider); 4 external calls (__init__, __init__, Lock, AsyncClient).


##### `rpc_metadata`  (lines 365–369)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the small set of gRPC metadata headers needed for Spectrum calls. Metadata is like request headers: extra information sent alongside the remote call.

**Data flow**: It receives a bearer token and an optional idempotency key. It always creates an authorization entry using the token, adds an idempotency entry when provided, and returns the entries as an immutable tuple.

**Call relations**: All gRPC operations use this before calling Spectrum: catch-up, subscribe, send text, send attachment, and download attachment. It keeps authentication header formatting in one place.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 372–410)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: Converts a raw Spectrum message-change event into the project’s simpler inbound-message object, but only if the event is a real incoming user message worth processing.

**Data flow**: It receives an unknown event object. It first checks that it is the expected Spectrum message-change type, then rejects anything that is not a received message or that came from this line. It filters out system, spam, service, corrupt, hidden, sticker, and empty messages. It finds the sender, collects visible non-sticker attachments, reads the text if present, and returns an `InboundMessage`; if anything essential is missing or irrelevant, it returns `None`.

**Call relations**: `catch_up` and `subscribe` call this while translating Spectrum event streams into provider events. It is the safety filter between noisy low-level iMessage events and the cleaner message objects the rest of the app wants to see.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### Connector permission backends
Core connector access rules and development-safe iMessage backing providers keep credentials and outbound behavior controlled.

### `core/src/ufo/runtime/access/connectors.py`

`domain_logic` · `cross-cutting: active during connector discovery, tool execution, and feed-sync authentication`

This file is about one sensitive question: when UFO needs to call an outside provider, where does the authentication secret live, and who is allowed to touch it? It creates a common set of shapes for connector backends, whether the backend is a broker such as Composio or Pipedream, or a direct “bring your own key” setup where the workspace stores an API key.

The main idea is that a connector receives a Credential, but that credential can take different safe forms. It may be a special HTTP transport that sends requests through a broker, so the local process never sees the real token. Or it may be a bearer token or custom headers read from the workspace credential store. The file is careful not to print these secrets by accident.

It also defines the ConnectorRegistry, which is like a reception desk for providers. Given a provider name, it finds the right broker, asks open broker catalogs for services, and falls back to direct credentials when needed.

The most protective part is source credential binding. A feed-sync source can be tied to a specific member-owned connection. Before each brokered request, the code checks the database to confirm that the same connection still belongs to the same workspace, member, provider, and account. If the connection was removed or changed, the request is stopped.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a Credential for debugging. It shows which kind of credential exists, but never shows the actual token, header value, or transport details.

**Data flow**: It reads the Credential object’s fields: transport, bearer, and headers. It chooses a short label based on which field is present, replaces any secret value with the word “redacted,” and returns that safe string.

**Call relations**: This method is used automatically by Python when a Credential is printed, logged, or included in an error display. Its job is defensive: even if some other code accidentally exposes local variables, the secret itself should not appear.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the common promise for anything that can turn a workspace, provider, and account into a usable Credential. Implementations may return a brokered transport, a bearer token, or custom authentication headers.

**Data flow**: The caller gives it a workspace ID, provider name, and account handle. An implementation checks whatever backend it owns, then returns a Credential object that the connector can use to build its HTTP client.

**Call relations**: This is a protocol method, meaning it describes what other classes must provide rather than doing the work itself. Feed-sync code calls through this shape so it does not need to know whether credentials came from a broker or a direct key store.


##### `GrantUnusable.__init__`  (lines 113–115)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an error for a connected account that cannot currently be used, usually because the member must reconnect it. It also records whether the system should wait specifically for a reconnect event before trying again.

**Data flow**: It receives a human-readable reason and an optional awaits_grant flag. It stores the reason in the normal exception machinery and keeps the flag on the exception object for later decision-making.

**Call relations**: Broker integrations such as Composio and Pipedream raise this when they discover an account grant is expired, revoked, unhealthy, or otherwise unusable. Higher-level sync code can then skip or park the feed instead of treating the problem like a temporary server outage.

*Call graph*: called by 4 (credential, _account, credential, _account).


##### `stale_grant_guidance`  (lines 118–125)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear message for the case where a broker does not recognize an old or moved account grant. The message tells the operator or member that reconnecting the account is the practical repair.

**Data flow**: It receives the provider name. It inserts that provider into a fixed explanation string and returns the finished guidance text.

**Call relations**: Broker code can use this helper when reporting stale grants. It keeps the wording consistent across broker implementations and avoids vague errors that would invite pointless retries.


##### `ConnectorBroker.tools`  (lines 196–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists tools for a provider. A tool here means an action the outside service can perform, such as searching messages or creating an issue.

**Data flow**: The caller supplies a workspace, provider, and search query. An implementation asks its broker backend for matching tools and returns BrokerTool records.

**Call relations**: This is part of the broker protocol used by dynamic connector tools. The registry routes provider requests to the right broker, and that broker implementation supplies the actual list.


##### `ConnectorBroker.schema`  (lines 200–200)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the argument shape for one specific tool. The schema tells the agent what information it must provide to run that tool safely and correctly.

**Data flow**: The caller gives a workspace, provider, and tool slug. An implementation returns a BrokerTool with its input schema filled in, or raises UnknownBrokerTool if the slug does not exist.

**Call relations**: Dynamic connector description flows call this after a tool name is chosen. It lets the agent prepare arguments without hard-coding every provider’s tool formats in core.


##### `ConnectorBroker.execute`  (lines 202–210)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool against a connected account. The broker, not the sandbox or agent, injects the real account token.

**Data flow**: The caller provides the workspace, provider, tool slug, arguments, account ID, and optional idempotency key. An implementation sends the request to the broker’s execution API and returns the provider response as a dictionary.

**Call relations**: Dynamic connector tools call this through the broker selected by ConnectorRegistry. It is the main execution seam: core chooses the route, while the broker integration performs the provider-specific call.


##### `ConnectorBroker.file_outputs`  (lines 212–212)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker points to files produced by a tool run. It returns references to downloadable files instead of moving file bytes through the main server process.

**Data flow**: It receives the raw response dictionary from a tool execution. An implementation looks inside that response, finds broker-produced file references, and returns BrokerFile objects with names and temporary URLs.

**Call relations**: After a brokered tool executes, higher-level tooling can call this to discover file outputs. The sandbox later fetches those URLs itself, so serve does not become a file relay.


##### `ConnectorBroker.stage_upload`  (lines 214–222)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares for a workspace file to be used as an input to a connector tool. It returns a temporary upload destination or an existing object reference.

**Data flow**: The caller supplies workspace, provider, tool slug, filename, MIME type, and file checksum. An implementation returns a StagedUpload telling the sandbox where to PUT the file and what argument value to pass to the tool.

**Call relations**: Dynamic connector tools use this before executing tools that need file inputs. The broker prepares storage references, and the sandbox transfers bytes directly to the broker’s file store.


##### `ConnectorBroker.search`  (lines 224–224)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic search over a broker’s tools. Instead of only matching names, the broker can return tools plus advice, plans, or warnings relevant to the user’s request.

**Data flow**: The caller sends a workspace, provider, and natural-language query. An implementation returns a BrokerSearch containing matching tools and optional guidance.

**Call relations**: Tool discovery features can call this when the broker has richer routing knowledge than a simple catalog. Core treats it as an optional smarter search surface behind the same broker interface.


##### `ConnectorBroker.credential`  (lines 226–226)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker supplies feed-sync authentication for one connected provider account. Usually this is a proxy transport, so the real provider token remains inside the broker.

**Data flow**: The caller gives a workspace ID, provider, and account handle. An implementation verifies the account and returns a Credential that the sync connector can use to make provider HTTP requests.

**Call relations**: _credential calls this when the source is using a broker-connected account rather than direct workspace credentials. Broker implementations must enforce that the account belongs to the workspace.


##### `GrantSecret.secret`  (lines 236–236)

```
async def secret(self, workspace_id: UUID, account_id: str) -> str
```

**Purpose**: Defines the rare path where this deployment is allowed to fetch the real provider token for a connected account. This is used for controlled proxy behavior, not for exposing the token to the sandbox.

**Data flow**: The caller gives a workspace and account ID. An implementation confirms the account belongs to that workspace and returns the secret token, or raises an error if it cannot authenticate it.

**Call relations**: CLI credential and egress proxy flows use this protocol when they must swap a safe placeholder for a real token at the network edge. It keeps token access behind an explicit narrow interface.


##### `ConnectorResolver.transfer_hosts`  (lines 306–306)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Names the broker file-store hosts that may be used for uploads and downloads in an open connector namespace. This helps the network layer know which extra hosts a grant should allow.

**Data flow**: An implementation returns a tuple of host names. No input is needed beyond the resolver’s own configuration.

**Call relations**: Connector resolver implementations provide this to the egress and connector setup around brokered file transfers. It supports safe file movement without opening arbitrary network access.


##### `ConnectorResolver.claims`  (lines 308–308)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open broker namespace serves a provider slug that was not explicitly registered. This avoids assuming that every unknown provider belongs to the broker.

**Data flow**: The caller supplies a provider name. An implementation checks its live catalog or routing rules and returns true or false.

**Call relations**: Code choosing between brokered connectors and workspace direct credentials can ask this before routing. It is important because an open namespace should still be proven, not treated as a catch-all.


##### `ConnectorResolver.entry`  (lines 310–310)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a ConnectorEntry for a provider claimed by an open broker namespace. The entry gives core a normal provider-to-broker route even when the provider was not listed at startup.

**Data flow**: It receives a provider slug. An implementation wraps that provider with a label and the shared broker, then returns a ConnectorEntry.

**Call relations**: ConnectorRegistry.entry and _broker call into the resolver when a provider is not found among explicit entries. This lets one broker serve many providers without pre-registering each one.


##### `ConnectorResolver.catalog`  (lines 312–312)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Returns a searchable page of connectable services from a broker’s live catalog. This lets the UI or discovery tool show services beyond the small set explicitly installed in the registry.

**Data flow**: The caller sends a query, a maximum result count, and an optional cursor for continuing a previous page. An implementation returns matching CatalogEntry records and a next cursor if more results exist.

**Call relations**: ConnectorRegistry.search_catalog and ConnectorRegistry.catalog use this to combine explicit connectors with the broker’s open catalog. The resolver owns the live broker-specific lookup.


##### `ConnectorRegistry.entry`  (lines 329–335)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the routing entry for a provider. It first checks explicitly registered connectors, then asks the open resolver, and fails clearly if nobody can serve that provider.

**Data flow**: It receives a provider name. It looks in the registry’s entries map; if absent, it asks the resolver to build an entry; if there is no resolver, it raises a KeyError.

**Call relations**: Dynamic connector tools use this kind of lookup when they need to dispatch a provider request to the correct broker. It is the registry’s main “which broker owns this provider?” answer.


##### `ConnectorRegistry.search_catalog`  (lines 337–342)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches only the open resolver’s live service catalog and returns its entries. If there is no open resolver, it returns an empty result.

**Data flow**: It receives a search query and limit. It asks the resolver for the first catalog page, extracts the entries, and returns them as a tuple.

**Call relations**: Discovery flows can use this to append open broker results to other known provider listings. It delegates all live searching to the resolver.


##### `ConnectorRegistry.catalog`  (lines 344–364)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Builds one combined catalog page from explicitly registered connectors and the open broker namespace. It also removes duplicates so a provider appears only once.

**Data flow**: It receives a query, result limit, and optional continuation cursor. On the first page, it locally filters explicit connector entries by provider and label. It also asks the resolver for its page, merges both lists, keeps the first entry for each provider, and returns a CatalogPage with the resolver’s next cursor.

**Call relations**: Connector discovery uses this when it needs a user-facing list of connectable services. It creates CatalogEntry and CatalogPage objects while combining local registry data with live resolver data.

*Call graph*: 2 external calls (__init__, __init__).


##### `_broker`  (lines 367–373)

```
def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None
```

**Purpose**: Finds the broker object that should serve a provider, if one is available. It is a small internal helper for credential routing.

**Data flow**: It receives the registry and provider name. It checks explicit entries first, then asks the resolver for an entry if present, and returns the broker; if neither route exists, it returns None.

**Call relations**: _credential calls this when a source uses a connected account. By keeping broker lookup in one helper, credential routing stays simple and consistent.

*Call graph*: called by 1 (_credential).


##### `_credential`  (lines 376–389)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right way to obtain a Credential for a feed-sync source. Connected accounts must go through a broker, while the special direct account goes through the configured fallback auth backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not the direct account, it finds a broker and asks that broker for credentials. If it is the direct account, it asks the fallback AuthProxy. If the needed route is missing, it raises an error.

**Call relations**: _BoundSourceCredentials.credential calls this after it has checked whether the source is allowed to use the requested account. _credential itself calls _broker for connected-account routing.

*Call graph*: calls 1 internal fn (_broker); called by 1 (credential).


##### `_require_source_connection`  (lines 392–416)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Verifies that a feed-sync source is still tied to the exact active connection it was created for. This prevents a source from silently using an account after ownership or connection state has changed.

**Data flow**: It receives workspace ID, connection ID, owner member ID, provider, and account. It opens a workspace-scoped database transaction, searches the connection table for a row matching all of those values, and returns nothing if found. If no matching row exists, it raises a ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before issuing connection-bound credentials. _ConnectionTransport.handle_async_request calls it again before each HTTP request, so authorization is checked not only at setup time but also at request time.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 428–436)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Wraps an HTTP transport with a last-second connection check. Before allowing a brokered provider request to leave, it confirms the source’s connection is still active and still belongs to the same member.

**Data flow**: It receives an outgoing HTTP request. It calls _require_source_connection using the transport’s stored workspace, connection, owner, provider, and account details. If the check passes, it forwards the request to the inner transport and returns that response.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper around broker-provided transports. The wrapper makes every later provider request re-check the database before handing off to the real transport.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 438–439)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is done with it. This releases whatever network resources the underlying transport owns.

**Data flow**: It takes no new information besides the wrapper object. It calls aclose on the inner transport and returns when that cleanup is complete.

**Call relations**: HTTP clients call this during cleanup. The wrapper does not own special cleanup of its own; it simply passes shutdown through to the transport it protects.


##### `_BoundSourceCredentials.credential`  (lines 448–478)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns credentials for a feed-sync source only if that source is allowed to use the requested account. It enforces the difference between direct credentials and member-owned broker connections.

**Data flow**: It receives workspace ID, provider, and account. For the direct account, it rejects the request if this source was bound to a connection, then delegates to _credential. For a brokered account, it requires stored connection and owner IDs, checks the database connection, asks _credential for broker credentials, requires those credentials to contain a proxy transport, and returns a new Credential whose transport is wrapped with _ConnectionTransport.

**Call relations**: SourceCredentialResolver.bind creates this object for a particular source. Its credential method calls _require_source_connection and _credential, then builds the protected transport wrapper that will keep checking authorization on each request.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 485–490)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy tied to one source’s connection information. This turns the general registry into a source-specific credential resolver with built-in ownership checks.

**Data flow**: It receives an optional connection ID and owner member ID. It returns a _BoundSourceCredentials object containing the registry and those binding values.

**Call relations**: The sync runner uses this when preparing credentials for a source. After binding, later credential requests go through _BoundSourceCredentials.credential rather than using the registry directly.

*Call graph*: 1 external calls (__init__).


### `extensions/imessage/ufo_ext_imessage/local_line.py`

`domain_logic` · `development-time provider flow, especially connection setup and listener runtime`

This file is a safe stand-in for a real iMessage provider. In a normal setup, the provider would assign phone numbers, stream incoming message events, send texts, send files, and download attachments. In a development setup without a real messaging backend, the rest of the app may still need something that looks like a provider so the connection flow can reach screens such as a QR code or an SMS link. `LocalLine` fills that gap.

Think of it like a display phone in a shop: it has a number printed on it, and it lets the surrounding process continue, but it is not connected to a real network. It always reports the same installation ID and always assigns the same fixed phone number. Its event streams deliberately produce no real events. The live subscription marks itself as ready, then waits forever, which keeps listener code alive without inventing fake messages.

The safety rule is important: anything that would actually deliver or retrieve message content fails immediately with a clear error. Sending a text, sending an attachment, or downloading an attachment all raise the same “local line delivers nothing” error. Cleanup and error-classification methods are no-ops or simple fixed answers, because this provider has no outside service state to repair or diagnose.

#### Function details

##### `LocalLine.installation_id`  (lines 18–19)

```
def installation_id(self) -> str
```

**Purpose**: Returns the fixed identity used for this local fake provider installation. This lets the rest of the system refer to the local provider in the same shape as a real provider.

**Data flow**: It takes no input besides the `LocalLine` object. It reads the constant local installation name and returns that string, without changing anything.

**Call relations**: Other provider-aware code can ask this property which installation it is talking to. Instead of consulting an external service, it simply gives back the local placeholder identity.


##### `LocalLine.assign_line`  (lines 21–22)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: Pretends to assign a messaging phone line, but always returns the same safe development number. This lets setup flows continue without provisioning a real number.

**Data flow**: It receives a requested phone number and an idempotency key, which would normally help avoid duplicate work. In this local version, both are ignored, and the fixed local phone number is returned. Nothing is stored or changed.

**Call relations**: When the connect or onboarding flow asks for a line, this method supplies the placeholder number. It does not call out to any provider, because this local provider is intentionally disconnected from real messaging infrastructure.


##### `LocalLine.catch_up`  (lines 24–26)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Represents the “catch up on missed provider events” step, but produces no events. It exists so code that expects an event stream can still run safely in local development.

**Data flow**: It receives an optional sequence marker saying where to resume from. It ignores that marker and ends immediately, so the caller gets no provider events and no state changes occur.

**Call relations**: Listener or synchronization code may call this before subscribing to live events. The function has an unreachable `ProviderEvent` yield only to make it behave like the expected asynchronous event stream shape; in practice, it hands back nothing.

*Call graph*: 1 external calls (__init__).


##### `LocalLine.subscribe`  (lines 28–31)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Starts a fake live event subscription that becomes ready but never receives messages. This keeps background listener code healthy without creating fake conversations.

**Data flow**: It receives an `asyncio.Event`, which is a small signal object used to tell another task that setup is complete. It sets that ready signal, then waits forever on a new event that is never triggered. No message events come out.

**Call relations**: The app’s listener code can call this when it wants to begin receiving provider events. This method tells the caller “I am ready” by setting the event, then deliberately stays silent forever; its unreachable `ProviderEvent` yield preserves the expected stream-like interface.

*Call graph*: 3 external calls (__init__, Event, set).


##### `LocalLine.send_text`  (lines 33–34)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: Refuses to send a text message through the local fake line. This protects developers and test environments from accidentally messaging a real phone.

**Data flow**: It receives a conversation ID, message text, and idempotency key. Instead of sending anything, it immediately raises an error saying the local line delivers nothing. No message ID is returned and no external state changes.

**Call relations**: Any higher-level messaging flow that tries to send through this provider will stop here. The clear failure makes it obvious that the local provider is only for connection flow and listener shape, not delivery.


##### `LocalLine.send_attachment`  (lines 36–43)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: Refuses to send a file attachment through the local fake line. Like text sending, this is a safety barrier against accidental real delivery.

**Data flow**: It receives a conversation ID, filename, file bytes, and idempotency key. It does not inspect or upload the file. It raises the fixed local-line error, so nothing is sent and no attachment or message ID comes back.

**Call relations**: If message-sending code tries to use the local provider for an attachment, this method is where the attempt is rejected. It mirrors `send_text` so both message types fail in the same predictable way.


##### `LocalLine.download_attachment`  (lines 45–47)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: Refuses to download an attachment from the local fake provider. Since the local line never receives real messages, there is nothing real to download.

**Data flow**: It receives an attachment ID. It immediately raises the fixed local-line error instead of returning file bytes. No network request, file lookup, or state change happens.

**Call relations**: Code that expects providers to offer attachment downloads can call this method, but in the local provider the path ends with a clear failure. The unreachable `yield` keeps the function shaped like an asynchronous byte stream, matching real providers.


##### `LocalLine.invalidate`  (lines 49–50)

```
async def invalidate(self) -> None
```

**Purpose**: Performs no cleanup because the local provider has no real session, token, cursor, or remote connection to invalidate.

**Data flow**: It receives only the `LocalLine` object. It returns successfully without reading external state or changing anything.

**Call relations**: Provider lifecycle code can call this during cleanup or reset just as it would for a real provider. For `LocalLine`, there is nothing to hand off to, so the method simply completes.


##### `LocalLine.invalid_cursor`  (lines 52–53)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: Says that an error is never an invalid-cursor problem for this provider. A cursor is a saved position in an event stream, and this local stream has no real position to lose.

**Data flow**: It receives an exception object. It does not inspect it in detail and always returns `false`, meaning the error should not be treated as a bad event-stream cursor.

**Call relations**: Error-handling code may ask this after something goes wrong while reading provider events. `LocalLine` answers that cursor recovery is never needed, because its event stream does not come from a real backend.


##### `LocalLine.external_error`  (lines 55–56)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: Says that an error is not a provider-side external service error. The local provider does not talk to an outside messaging service, so there is no outside service to blame.

**Data flow**: It receives an exception object and always returns `false`. It does not change anything and does not classify any error as external.

**Call relations**: Higher-level error reporting can call this when deciding how to label a failure. For the local provider, it keeps the answer simple: failures are not remote provider outages.


##### `LocalLine.error_code`  (lines 58–59)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: Returns the fixed error code used for local-line failures. This gives the rest of the system a stable label for errors raised by this fake provider.

**Data flow**: It receives an exception object but does not need to inspect it. It returns the constant local-line error code string and changes nothing.

**Call relations**: When error-handling or API code needs a short machine-readable label, it can call this method. `LocalLine` always hands back the same code so local-provider failures are easy to recognize.


### Slack lifecycle hooks
Slack-specific hooks add connector polish around message sending and completed account connections.

### `extensions/slack/ufo_ext_slack/hooks.py`

`orchestration` · `hook handling before tool use and after connection recording`

This file is like a small backstage crew for Slack-related events. Before an external connector sends a Slack message, it checks whether the message should include a footer that mentions the actual Slack bot user for this workspace. That matters because the generic connector tool does not know the Slack bot’s identity, but the Slack extension does. If the extension can safely read the bot user ID from its own stored data, it rewrites the message arguments so the footer names the bot correctly. If anything goes wrong, it does nothing and lets the normal generic footer happen instead. This is important because this hook runs before a tool call, and a slow or failing hook could otherwise stop the Slack message from being sent.

The file also cleans up Slack “connect” buttons. When a member clicks a button to connect an account and the connection is successfully recorded, the old button in Slack should no longer invite them to do something already finished. The hook looks up the saved Slack message, updates it to show the connected account, and then removes the saved reference. If Slack updating fails, the account connection itself is still already done; only the visual Slack message is stale.

#### Function details

##### `attribute_connector_send`  (lines 38–52)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook checks whether an outgoing connector call is really a Slack send, and if so, tries to add a footer that mentions the correct Slack bot user. It leaves all unrelated tool calls, and Slack sends without a known bot user, exactly as they were.

**Data flow**: It receives a hook context containing the event payload. If the payload is a pre-tool-use event for the connector tool and the tool call looks like a Slack send, it asks `_mirrored_self_user_id` for the stored Slack bot user ID. If an ID is available, it passes the outgoing arguments through `mention_attributed`, builds a modified version of the tool input, and returns that change. If the event is not a Slack send, or the bot user ID cannot be read, it returns no change.

**Call relations**: This function is called by the hook system before an external tool is used. It relies on `is_slack_send` to recognize Slack sends, `_mirrored_self_user_id` to safely fetch the local bot identity, and `mention_attributed` to rewrite the message text. It hands the finished rewrite back as a `ModifyInput` outcome so the connector tool receives the improved arguments.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 55–69)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper safely reads the Slack bot user ID that the Slack surface previously stored for this workspace. It is deliberately cautious: if the read is slow, fails, or returns something that does not look like a Slack bot user ID, it returns nothing instead of risking a blocked message send.

**Data flow**: It receives the hook context and reads from the extension’s scoped store using the known storage key. The read is wrapped in a short timeout, so it has only a small window to finish. If an exception happens, it logs the error type and returns `None`. If the stored value is a string matching the expected Slack bot user ID pattern, it returns that string; otherwise it returns `None`.

**Call relations**: This helper is used by `attribute_connector_send` when a Slack connector send might need a named bot footer. It calls `asyncio.timeout` to keep the hook from waiting too long, uses `re.match` to verify the stored ID shape, and logs unreadable store cases through the project’s observability logger.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


##### `settle_connect_button`  (lines 72–101)

```
async def settle_connect_button(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook updates a Slack connect button after an external account connection has succeeded. Instead of leaving a button that still says “connect,” it changes the Slack message to show the account that was connected.

**Data flow**: It receives a hook context and expects its payload to say that a connection was recorded, including the provider, account ID, optional account label, and owning member. It builds the storage key for that member and provider, reads the saved Slack message reference, and stops if none exists. If a saved message is found, it gets the Slack bot token, validates the saved message data, calls Slack-facing code to settle the message into its completed state, and then deletes the stored reference.

**Call relations**: This function is called by the hook system after a connection has been recorded. It uses `connect_message_key` to find the saved button message, `ConnectMessage.model_validate` to turn stored data back into a safe message object, and `settle_connect_message` to update Slack. If invoked with the wrong kind of payload, it raises an error because this hook only makes sense for completed connection records.

*Call graph*: 3 external calls (model_validate, connect_message_key, settle_connect_message).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-auth-tokens-sessions` — The login, surface, sandbox, and signing tokens that prove who a request belongs to and what it may access.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-surface-routing-state` — The saved routing information that maps web, Slack, iMessage, terminal, hosted app, and public-link traffic to the right workspace and conversation.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-source-sync-state` — The saved state for connected information sources, including cursors, pages, deletions, warnings, backoff, and source access grants.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-human-request-state` — Pending and resolved human-interaction requests, including agent questions, secret requests, credential requests, and connection-authorization handoffs.
- `reg-product-census-telemetry` — Derived product analytics/census state summarizing workspace activity, onboarding progress, tool connections, and payment funnel status for dashboards.
- `reg-external-client-connection-pools` — Process-global HTTP/gRPC client sessions, proxy clients, DNS/TLS state, and connection pools used for model providers, connectors, cloud storage, and sandbox services.
- `reg-provider-rate-limit-backoff` — Shared throttling, retry-after, backoff, and concurrency state for AI providers and external connector APIs, separate from billing spend caps.
- `reg-tool-bridge-invocation-state` — Durable request/result state for sandbox-to-host tool bridge calls, including pending bridge invocations, approvals, idempotency, and returned outputs.

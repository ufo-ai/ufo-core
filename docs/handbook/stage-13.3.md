# Composio Brokered Connector Integration  `stage-13.3`

This stage is shared behind-the-scenes support for using outside apps through Composio. Composio is a hosted service that keeps user account tokens and offers tools for services like GitHub or Google. UFO uses it as a safe middleman, so workspaces can use connected accounts without seeing secret credentials.

The client is the main doorway to Composio. It creates connection links, checks which accounts are connected, searches available tools, uploads files, and asks Composio to run a tool. The provider adapts UFO’s normal login flow to Composio’s link-based connection process. The resolver acts like a front desk: when someone asks for a toolkit, it checks whether Composio supports it and routes the request to the shared broker.

The broker is the central machine operator. It exposes Composio tools to UFO, prepares uploads, collects file outputs, runs brokered actions, and keeps provider calls safe. The proxy turns ordinary HTTP requests into Composio proxy calls and returns ordinary-looking responses. The MCP session handles one short tool-router call over MCP, a simple protocol for calling remote tools, and normalizes the reply.

## Files in this stage

### Connector Entrypoints
Top-level broker and resolver code expose Composio-backed tools to UFO and route toolkit requests through the shared integration path.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling and connector tool execution`

Composio is treated here like a large tool marketplace. This file turns Composio’s own API shapes into the simpler broker shapes that UFO expects. Without this bridge, an agent could not reliably ask “what tools are available?”, “what inputs does this tool need?”, “run this tool for this workspace account,” or “where did the output file go?”

The main class, ComposioBroker, is deliberately stateless. Each method asks for the current Composio client when it runs, instead of keeping one forever. That matters for tests and for safe connection use: it is like checking out a fresh library card at the desk instead of carrying an old one that may no longer work.

The broker also adds safety and helpfulness around errors. If a tool slug is wrong, it tries to return a clearer message with real available tool names. If a connected account has gone stale, it tells the user to reconnect instead of pretending the tool is missing. For credentials, it does not return a token. It returns a Credential whose network transport proxies calls through Composio, so downstream code can make provider requests without directly holding the provider secret.

File handling is another important part. Tool responses may contain Composio file objects anywhere inside nested data, and this file searches for them. For uploads, it asks Composio for a temporary upload slot and returns the exact argument shape the tool should receive afterward.

#### Function details

##### `ComposioBroker.tools`  (lines 49–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Composio tools for a provider that match a user or agent query, then presents them in UFO’s standard broker tool format. Someone would use this when an agent is deciding what actions are available.

**Data flow**: It receives a workspace id, provider name, and search text. It asks the current Composio client for matching tools, then passes Composio’s raw rows through the local converter that trims and normalizes them. It returns a tuple of BrokerTool objects that the rest of UFO can understand.

**Call relations**: This is the discovery doorway for Composio tools. It calls the shared Composio client to fetch the catalog and hands the response to _discovered_tools so callers do not have to understand Composio’s raw listing format.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–66)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the full input schema for one specific Composio tool. This tells the agent what information the tool needs before it can be run.

**Data flow**: It receives a workspace id, provider, and tool slug. It asks Composio for that tool’s schema, converts any file-upload fields into UFO’s workspace-file vocabulary, checks whether the tool is marked read-only, and returns a BrokerTool. If Composio says the slug does not exist, it raises UnknownBrokerTool so the caller gets a clear “that tool is unknown” signal.

**Call relations**: This is used after a tool has been chosen or inspected more closely. It gets raw schema data from the Composio client, relies on workspace_file_schema to make file inputs usable by UFO’s dynamic tools, and uses _read_only to preserve Composio’s safety hint.

*Call graph*: calls 1 internal fn (_read_only); 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 68–91)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a chosen Composio tool for this workspace’s connected account. It also turns two common confusing failures into better guidance: stale accounts become reconnect messages, and missing tool slugs can include suggested real tool names.

**Data flow**: It receives the workspace id, provider, tool slug, arguments, connected account id, and optional idempotency key. It builds the broker user id for the workspace, sends the execution request to Composio, and returns the response dictionary. If Composio reports an error, it checks whether the account is stale, whether the slug is missing, or whether the original error should simply be passed on.

**Call relations**: This is the main run path for Composio tools. It calls _stale_account before treating a 404 as a missing slug, because a dead account should lead to reconnection guidance, not a list of tools. If the slug really appears to be missing, it asks _slug_miss to enrich the error.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 93–98)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a Composio tool response. This lets UFO notice downloadable outputs even when they are buried inside nested response data.

**Data flow**: It receives the response dictionary from a tool run. It creates an empty list, asks _collect_files to walk through the whole response, and returns all found files as BrokerFile objects in a tuple. It does not change the original response.

**Call relations**: This is called after tool execution when the system wants to expose generated files to the workspace. It delegates the nested searching to _collect_files, which knows the Composio file marker shape.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 100–116)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a safe temporary upload destination for a file that will be passed into a Composio tool. This is needed because the tool expects a Composio file reference, not raw local bytes.

**Data flow**: It receives the workspace id, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL where bytes should be uploaded, the content type to use, and the tool argument that should later refer to the uploaded file.

**Call relations**: This runs before executing tools that take file inputs. It calls the Composio client to reserve the upload location, then packages the result in UFO’s standard StagedUpload form so the upload and later tool call can be coordinated.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 118–121)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Uses Composio’s Tool Router search to find connector tools relevant to a query. This is a broader search path than simply listing tools from the catalog.

**Data flow**: It receives a workspace id, provider, and query. It gets the current Composio client and passes all of that to Composio’s search helper. It returns a BrokerSearch result for the caller to use in tool selection.

**Call relations**: This function is a thin bridge into Composio’s search feature. It keeps the broker interface consistent while handing the actual search work to search_connector_tools.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 123–139)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe credential object for making provider HTTP calls through Composio’s proxy, without exposing the provider’s actual secret token. It first confirms that the requested connected account belongs to this workspace’s broker user.

**Data flow**: It receives the workspace id, provider, and connected account id. It builds the expected broker user id, asks Composio to confirm the account is connected for that user and provider, and then returns a Credential with a ComposioProxyTransport inside it. If Composio cannot find that account, it raises GrantUnusable with instructions to reconnect.

**Call relations**: This protects against a confused-deputy problem, where one workspace might accidentally use another account’s grant. It calls Composio to verify ownership, uses stale_grant_guidance for human-friendly reconnect advice, and wraps the result in ComposioProxyTransport so later HTTP requests are proxied through Composio.

*Call graph*: calls 1 internal fn (__init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, composio_client).


##### `ComposioBroker._slug_miss`  (lines 141–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by adding real tool slugs that are available for the provider, when it can. This helps the agent recover from a bad tool name on the next attempt.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the bad slug into search words, asks Composio for nearby tools, and falls back to listing tools if the search finds nothing. If it finds tools, it returns a new ComposioError with the original message plus suggestions; if anything goes wrong or no tools are found, it returns the original error.

**Call relations**: This is only used by ComposioBroker.execute after execution failed with a not-found status that was not identified as a stale account. It calls list_tools and _discovered_tools so the final error can speak in the same tool format used by normal discovery.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Recursively searches any nested response data for Composio file objects. A Composio file object is recognized by having a file URL, MIME type, and name.

**Data flow**: It receives any value and a list being used to collect results. If the value is a matching file-shaped dictionary, it adds a BrokerFile with the file name and URL. If the value is a dictionary or list, it walks through its contents and repeats the same check. It returns nothing, but it grows the provided list.

**Call relations**: ComposioBroker.file_outputs starts the search with the whole tool response and an empty list. _collect_files does the deep walk, creating BrokerFile entries whenever it finds Composio’s file marker shape.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error is really saying that the connected account is gone or no longer usable. This prevents the system from mistaking a dead account for a missing tool.

**Data flow**: It receives a Composio error and the account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account: Composio’s own “connected account not found” wording, or the specific account id together with “not found.” It returns true only for those stale-account patterns.

**Call relations**: ComposioBroker.execute calls this before handling not-found errors as slug misses. If this returns true, execute routes the error to _reconnect_error so the user is told to reconnect the provider account.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a clearer Composio error that tells the user the provider account likely needs to be reconnected. It preserves the original status while adding practical next-step guidance.

**Data flow**: It receives the original Composio error and provider name. It combines the original error body with provider-specific stale-grant guidance, then returns a new ComposioError with that expanded message.

**Call relations**: ComposioBroker.execute uses this when _stale_account identifies a dead or missing connected account. It relies on stale_grant_guidance to produce the human-facing reconnect advice.

*Call graph*: called by 1 (execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 195–216)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio’s raw tool-list rows into UFO’s BrokerTool objects. This gives callers a clean list with real slugs, short descriptions, input schemas, and read-only hints.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses the slug from the row’s slug or name field, skips rows without a usable slug, trims long descriptions, converts input parameters into UFO’s workspace-file-aware schema, checks the read-only tag, and returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal tool discovery, and ComposioBroker._slug_miss uses it when preparing suggestions after a failed execute call. It calls _read_only and workspace_file_schema so discovered tools match the same conventions as full schemas.

*Call graph*: calls 1 internal fn (_read_only); called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


##### `_read_only`  (lines 219–221)

```
def _read_only(payload: dict[str, object]) -> bool
```

**Purpose**: Checks whether Composio marked a tool as read-only. A read-only tool is expected to inspect or fetch information rather than change outside state.

**Data flow**: It receives a payload dictionary. It looks at the payload’s tags field and returns true only if that field is a list containing the read-only marker. Otherwise, it returns false.

**Call relations**: ComposioBroker.schema and _discovered_tools both call this while building BrokerTool objects. That keeps the read-only signal consistent whether the tool came from a detailed schema lookup or from a discovery list.

*Call graph*: called by 2 (schema, _discovered_tools).


### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect flow`

Composio provides access to many outside services, called toolkits. Instead of listing every possible service in this project’s own connector registry, this file creates an “open namespace”: a way for a user to ask for a toolkit by its slug, such as a short service name, and have the system decide at runtime whether Composio can support it.

The central piece is `ComposioResolver`. It is small and intentionally stateless, meaning it does not keep a long-lived client connection or cached account data. Each time it needs Composio, it asks for the current Composio client. That matters for tests and configuration changes, because a changed client or transport can be picked up immediately.

The resolver first refuses locally banned provider names. For anything else, it checks Composio’s live catalog to see whether the toolkit is connectable. If it is, the resolver can build an OAuth provider description. OAuth is the common “sign in and grant access” flow used by many services, but here the service token stays with Composio and tools run through Composio’s side. The resolver also creates a connector entry that points every accepted toolkit to the same shared broker, and it exposes catalog search so discovery only shows services that can actually be connected.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-transfer hosts are allowed. It matters when tools need to move files in or out through the sandbox instead of reaching arbitrary internet locations.

**Data flow**: It takes the resolver object as its only input, reads the predefined Composio transfer host list, and returns that list as a tuple of host names. It does not change anything.

**Call relations**: When the connector system needs to know what file-store hosts a Composio-backed grant may use, it reads this property. The value comes directly from the Composio client constants, so this resolver stays in step with the Composio transport rules.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function answers the question, “Is this provider name one that Composio can take responsibility for?” It prevents banned names from being accepted and checks Composio’s live catalog for everything else.

**Data flow**: It receives a provider slug as text. First it lowercases the slug and checks it against the local banned list; if it is banned, the answer is immediately false. Otherwise it asks the current Composio client whether that toolkit is connectable, and returns true only if Composio finds a matching toolkit.

**Call relations**: During connector resolution, this is the gatekeeper for the open Composio namespace. It calls `ufo_ext_composio.client.composio_client` to get the active client, then uses that client to confirm the toolkit with Composio before later steps are allowed to build a descriptor or entry.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth description used to start a connection for a validated Composio toolkit. The descriptor says which provider slug is being connected, while leaving the host empty because Composio keeps and uses the service token on its own side.

**Data flow**: It receives a provider slug and uses it to create a `ComposioOAuthProvider`. The result is an OAuth provider object that the connect flow can use; the resolver itself does not store it.

**Call relations**: After a provider has been accepted, the connect flow can ask this resolver for the connection descriptor. This function hands off to `ComposioOAuthProvider.__init__`, which creates the provider-specific OAuth object for the rest of the connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the registry entry that tells the system how to route a Composio toolkit once it has been accepted. Every toolkit slug is pointed to the same shared `ComposioBroker`, which is the component that later runs the tools through Composio.

**Data flow**: It receives a provider slug. It turns that slug into a human-friendly label by replacing underscores with spaces and title-casing the words, then returns a `ConnectorEntry` containing the original provider name, the label, and this resolver’s broker.

**Call relations**: When the connector registry needs an actual entry for a claimed Composio provider, this function builds it. It calls `ConnectorEntry.__init__` to package the provider name and shared broker so later tool execution can be routed through the broker.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–51)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT, after: str | None=None) -> CatalogPage
```

**Purpose**: This function searches Composio’s toolkit catalog so users can discover services they may connect. It returns a page of connectable toolkit results rather than making the project maintain its own giant list.

**Data flow**: It receives a search query, a maximum number of results, and optionally an `after` marker used to fetch the next page. It asks the current Composio client to list matching toolkits, then returns the catalog page from Composio.

**Call relations**: Discovery tools call this when they need searchable Composio options. Like `claims`, it calls `ufo_ext_composio.client.composio_client` to use the currently configured client, then delegates the real catalog lookup to Composio.

*Call graph*: 1 external calls (composio_client).


### Composio Account and Tool Calls
Client, MCP, and provider helpers manage hosted account connection flows, tool discovery, file transfer, and individual tool execution through Composio.

### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector auth, discovery, and tool execution`

Composio acts like a secure switchboard for outside services. Instead of this project storing a separate password or access token for every service, Composio keeps those secrets and this client talks to Composio's web API. Without this file, the connector system could not safely let a workspace connect an outside account, discover what actions are available, or run those actions through Composio.

The file has three main jobs. First, it decides which Composio toolkits are allowed here. Some are blocked because their tools are missing key abilities or only work read-only. Second, it performs the account connection flow: it finds or creates an authentication setup, makes a hosted OAuth login link, and later verifies that the returned connected account belongs to the expected workspace user and toolkit. OAuth is the common web sign-in flow where a user grants access without sharing their password.

Third, it supports tool discovery and execution. It can list catalog tools, fetch one tool's schema, ask Composio's Tool Router for semantic search results, prepare upload slots for files, and execute a tool on Composio's servers. A helpful detail is that file-upload inputs are rewritten into a simple workspace-file path format, so the model names a file in the workspace rather than learning Composio's storage format.

#### Function details

##### `connectable`  (lines 130–154)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit is safe and useful for this deployment to offer to users. It blocks known-bad toolkits, toolkits with no Composio-managed login method, and toolkits with no tools.

**Data flow**: It receives a toolkit slug, such as a provider name, and a catalog record from Composio. It checks the local banned list, then reads the record's managed authentication schemes and tool count. It returns true only when the toolkit has usable managed authentication, at least one tool, and is not locally banned.

**Call relations**: When the client is asked whether one toolkit can be claimed, ComposioClient.connectable_toolkit uses this function as the final gate. When listing many toolkits, ComposioClient.list_toolkits uses it to filter the catalog before showing entries to callers.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 161–164)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Builds a clear error for a failed or unusable Composio API response. It keeps both the HTTP status number and the response text so callers can report what went wrong.

**Data flow**: It receives a status code and response body text. It turns them into a readable exception message and stores the original pieces on the error object. The output is an exception ready to be raised.

**Call relations**: Low-level response parsing uses this when Composio returns an error or an unexpected shape. Higher-level methods also use it when required fields, such as a redirect URL or upload key, are missing.

*Call graph*: called by 6 (_account, _auth_config, connect_link, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 181–190)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to connect an outside account through Composio. This is the start of the consent flow.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates the Composio authentication configuration for that toolkit, then posts those details to Composio. It returns the redirect URL that should be shown to the user, or raises an error if Composio did not provide one.

**Call relations**: This method starts by calling ComposioClient._auth_config so the login flow has an authentication setup to use. It then sends the link request through ComposioClient._post, and uses ComposioError if the response is malformed.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 192–196)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a connected Composio account is valid for the expected workspace user and toolkit. It returns the small account reference this project stores as the grant.

**Data flow**: It receives a Composio account id, the expected user id, and the expected toolkit. It asks ComposioClient._account to verify ownership, active status, and toolkit match. If verification passes, it returns an OAuthAccount containing only the connected-account id.

**Call relations**: This is the public wrapper around ComposioClient._account. It is used after the user finishes the connection flow, so the system stores a safe account reference rather than a secret token.

*Call graph*: calls 1 internal fn (_account); 1 external calls (__init__).


##### `ComposioClient._account`  (lines 198–227)

```
async def _account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> dict[str, object]
```

**Purpose**: Fetches a connected account from Composio and checks that it is the exact account this workspace is allowed to use. It prevents accepting an inactive, foreign, or wrong-toolkit account.

**Data flow**: It receives an account id, an expected user id, and an expected toolkit. It reads the account record from Composio, compares the owner, checks that the status is ACTIVE, and verifies the toolkit slug. It returns the account payload when all checks pass, or raises a clear error when something is unsafe or unusable.

**Call relations**: ComposioClient.connected_account calls this before creating the local OAuthAccount. It relies on ComposioClient._get for the API read, raises ComposioError for ownership or toolkit mismatches, and raises GrantUnusable when the account exists but needs the user to reconnect.

*Call graph*: calls 3 internal fn (__init__, _get, __init__); called by 1 (connected_account).


##### `ComposioClient.account_label`  (lines 229–232)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Gets a human-friendly name for a connected account, if Composio has one. This can be used to show users which account is connected.

**Data flow**: It receives a connected account id and fetches that account record from Composio. It reads the alias field and returns it only if it is a non-empty string. If there is no useful alias, it returns null.

**Call relations**: This is a small read helper built on ComposioClient._get. It does not change anything; it only asks Composio for account details and extracts the display label.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 234–264)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the tools Composio offers for one toolkit, optionally filtered by a search query. It follows Composio's pages so tools beyond the first page are not missed.

**Data flow**: It receives a toolkit slug and optional query. It repeatedly requests pages from Composio, collecting valid tool records until there are no more pages or the local maximum is reached. It returns a tuple of tool dictionaries.

**Call relations**: ComposioBroker._slug_miss calls this when it needs to discover whether a tool slug exists or to look through available tools. Internally, this method repeatedly uses ComposioClient._get to walk the paged Composio catalog.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 266–267)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed schema for one Composio tool. A schema describes what inputs the tool accepts and what the tool is for.

**Data flow**: It receives a tool slug. It sends a GET request for that tool and returns the response dictionary from Composio. It does not reshape the response itself.

**Call relations**: This is a direct catalog lookup built on ComposioClient._get. Other connector code can call it when it needs the full description of a specific tool rather than a list.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 269–286)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a user-supplied toolkit slug is a real, safe, connectable Composio toolkit. It also returns the display name to show if the toolkit is accepted.

**Data flow**: It receives a slug string. It first rejects slugs with unsafe characters so the value cannot become a strange API path. It then fetches the toolkit from Composio, treats not-found as not connectable, runs the local connectable check, and returns the toolkit name or slug. If the toolkit is not suitable, it returns null.

**Call relations**: This method combines a Composio lookup through ComposioClient._get with the local connectable policy. It is the gate used before this deployment claims that it can broker a toolkit.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 288–315)

```
async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads one page of Composio's toolkit catalog and returns only the entries this deployment is willing to offer. It is used for browsing or searching available connectors.

**Data flow**: It receives a search query, a page size, and an optional cursor that means 'continue after here.' It asks Composio for a page, filters out malformed or non-connectable items, converts accepted items into CatalogEntry objects, and returns them with the next cursor in a CatalogPage.

**Call relations**: This method uses ComposioClient._get to read the catalog and connectable to apply the local policy. It hands callers a clean CatalogPage instead of raw Composio response data.

*Call graph*: calls 2 internal fn (_get, connectable); 2 external calls (__init__, __init__).


##### `ComposioClient.execute_tool`  (lines 317–331)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Asks Composio to run one tool on its own servers. This keeps the user's real service token inside Composio instead of sending it through this project.

**Data flow**: It receives a tool slug, argument values, a user id, and optionally a connected account id and idempotency key. It builds the request body, rejects it if it is too large, adds the idempotency header when provided, and posts the execution request to Composio. It returns Composio's response dictionary.

**Call relations**: This is the main execution path for connector tools. It uses json.dumps to measure the outgoing payload size, then ComposioClient._post to send the request.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 333–357)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Creates a temporary Composio upload slot for a file that a tool needs. The tool later receives Composio's file key rather than raw file bytes.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts an upload request to Composio, reads the returned storage key, and optionally reads a presigned PUT URL where the sandbox can upload bytes. It returns a ComposioUpload with the key and possibly the upload URL.

**Call relations**: This method uses ComposioClient._post to ask Composio where to stage the file. It raises ComposioError if Composio leaves out the required key or gives a malformed URL, and returns a ComposioUpload for the caller to use.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 359–370)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. Semantic search means the user can ask in normal language, and Composio suggests relevant tools.

**Data flow**: It receives a user id and a list of toolkits to enable. It posts that scope to Composio, then reads the session id and MCP URL from the response. It returns a ToolRouterSession, or raises an error if those fields are missing.

**Call relations**: search_connector_tools calls this when no cached session exists for the user and connector. The returned MCP URL is then used to call Composio's search tool.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 372–390)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration Composio should use for a toolkit, creating a managed one if needed. This lets the OAuth connection flow work even when no custom setup exists.

**Data flow**: It receives a toolkit slug. It asks Composio for an existing auth configuration and returns the first id if found. If none exists, it asks Composio to create a managed authentication configuration, extracts its id, and returns that id.

**Call relations**: ComposioClient.connect_link calls this before creating a user login link. It uses ComposioClient._get to look for an existing setup, _auth_config_id to read an id from the list response, ComposioClient._post to create one when needed, and ComposioError if creation gives no id.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 392–394)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a GET request to Composio and returns a checked response body. GET is the usual web request used to read data.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the request, passes the response through _body, and returns the parsed dictionary. It closes the HTTP client after the request.

**Call relations**: All read-style client methods use this helper, including account checks, auth-config lookup, catalog listing, toolkit lookup, and schema lookup. It relies on ComposioClient._http to build the configured HTTP client and _body to turn the response into safe data or an error.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_account, _auth_config, account_label, connectable_toolkit, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 396–400)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a POST request to Composio and returns a checked response body. POST is the usual web request used to create something or trigger an action.

**Data flow**: It receives an API path, a JSON body, and optional headers. It opens an HTTP client, sends the JSON request, passes the response through _body, and returns the parsed dictionary. It closes the HTTP client after the request.

**Call relations**: Client methods use this for creating login links, creating auth configs, executing tools, creating uploads, and opening Tool Router sessions. It relies on ComposioClient._http for connection setup and _body for response validation.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 402–408)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Creates the configured asynchronous HTTP client used to talk to Composio. Asynchronous means the program can wait for the network without blocking all other work.

**Data flow**: It reads the client's API key and optional test transport from the ComposioClient object. It builds an httpx AsyncClient with the Composio base URL, API key header, timeout, and transport. It returns that client to be used inside a request.

**Call relations**: ComposioClient._get and ComposioClient._post call this for every request. Creating a fresh client per request also lets tests inject a mock transport and helps avoid leaked connections.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 411–419)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a normal dictionary, or raises a clear error. It is the shared response checker for the client.

**Data flow**: It receives an httpx Response. If the status code is an error, it raises ComposioError with the response text. If the response is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is an object-like dictionary.

**Call relations**: ComposioClient._get and ComposioClient._post both hand every response to this function. It centralizes the rule that Composio responses must be successful and shaped like objects.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 422–448)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the simpler format this agent understands: a workspace file path. This hides Composio's storage details from the model.

**Data flow**: It receives any schema value, which may be a dictionary, list, or plain value. When it finds a dictionary marked as file-uploadable, it replaces it with a small object requiring the workspace file path. For nested dictionaries and lists, it walks through them and rewrites any uploadable parts, returning the transformed schema.

**Call relations**: _search_result calls this while building BrokerTool objects from Composio Tool Router results. That way, searched tools show file inputs in the project's own vocabulary rather than Composio's raw file-reference format.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 451–458)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small safety helper for reading a nested response shape.

**Data flow**: It receives a response dictionary. It looks for an items list, then returns the first item's id when that id is a string. If the response does not contain a usable id, it returns null.

**Call relations**: ComposioClient._auth_config calls this after asking Composio for existing auth configurations. If this helper finds no id, _auth_config goes on to create a new managed configuration.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 461–468)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the default ComposioClient for this deployment using the API key from the environment. It fails early if the key is missing.

**Data flow**: It reads the COMPOSIO_API_KEY environment variable. If the value is missing or empty, it raises a runtime error explaining that Composio OAuth cannot be brokered. Otherwise it returns a ComposioClient configured with that key.

**Call relations**: Other parts of the extension can call this when they need the real deployment client. It constructs ComposioClient directly and makes configuration failure loud rather than letting later API calls fail mysteriously.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 475–476)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. It prevents malformed Composio search data from breaking the parsing code.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: _search_result uses this repeatedly while walking Tool Router output. It keeps missing or oddly shaped nested fields from causing crashes.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 479–482)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It filters out missing or wrongly typed items.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only non-empty strings and returns them as a tuple.

**Call relations**: _search_result uses this to read tool slugs, plan steps, guidance, and pitfalls from Tool Router results without trusting every field blindly.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 485–519)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router search output into the project's standard BrokerSearch shape. It gathers suggested tools plus the plan, guidance, and warnings that help the model choose what to do next.

**Data flow**: It receives a result dictionary from the Tool Router. It reads nested search results and tool schemas, deduplicates tool slugs, rewrites file-upload schemas into workspace-file schemas, and builds BrokerTool objects. It returns a BrokerSearch containing the tools, recommended plan steps, execution guidance, and known pitfalls.

**Call relations**: search_connector_tools calls this after the MCP search call returns. This function relies on _dict and _str_tuple for safe parsing and workspace_file_schema so file inputs are shown in the local format.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 522–545)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches a connector's Composio tools using natural-language intent. It lets the system ask, for example, 'find a tool to create an issue,' and receive relevant tool choices and advice.

**Data flow**: It receives a ComposioClient, workspace id, connector slug, and query text. It builds the broker user id, reuses or creates a cached Tool Router session for that user and connector, calls Composio's MCP search tool with the query, and converts the result into BrokerSearch. It also updates the in-memory session cache when a new session is opened.

**Call relations**: This is the top-level semantic search helper in the file. It calls ComposioClient.tool_router_session when needed, sends the actual search through mcp_session.mcp_call_tool, and hands the raw result to _search_result for shaping.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

Composio exposes tool search through an MCP endpoint. MCP, or Model Context Protocol, is a standard way for an app to talk to external tools. This file is the small bridge between this project and that endpoint.

Its job is deliberately narrow: open a temporary HTTP-based MCP session, call one named tool with some arguments, close the session, and return the answer in a plain dictionary. In everyday terms, it is like walking up to a service desk, asking one question, writing down the answer in a standard form, and leaving.

The response from an MCP tool can arrive in more than one format. Sometimes it is already parsed data. Sometimes it is called structured content. Sometimes it is a text block containing JSON. This file checks those options in order and picks the most useful one. If it only gets plain text, it wraps that text in a dictionary so callers still get the same kind of result.

A key point is that this is for search through Composio's Tool Router, not for executing the final tool action. That keeps execution, permissions, and metering on Composio's normal execute API. The imported Client is kept as a module-level name so tests can replace it with a fake client instead of calling a real network service.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: This function calls one MCP tool at a given HTTP endpoint and returns the result as a plain dictionary. It is used when the extension needs to ask Composio's Tool Router for structured information, such as search results.

**Data flow**: It receives an endpoint URL, a tool name, input arguments, HTTP headers, and a timeout. It opens a streamable-HTTP MCP client with those settings, sends the tool call, then closes the client session. After the response comes back, it looks first for already-parsed dictionary data, then for structured dictionary content, then for a text block it can parse as JSON. The output is always a dictionary, either containing the structured response, parsed JSON, plain text under a text key, or a fallback result value.

**Call relations**: When higher-level Composio code needs one Tool Router lookup, it calls this function instead of dealing with MCP connection details itself. Inside, this function creates the FastMCP HTTP transport and client, asks the client to call the named tool, and uses JSON parsing only if the response arrives as text rather than already-structured data.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `connect flow`

ufo expects an OAuth provider to give it a simple authorization URL, send the user to a consent page, then later exchange a returned code for an account. Composio does not work quite that directly: first ufo must ask Composio to create a one-time connect link, and that request is asynchronous. This file solves that mismatch by adding a small browser bridge route.

The flow starts when ufo asks `ComposioOAuthProvider.authorize_url` for a URL. Instead of pointing straight at Composio, it points the browser to this extension's own `/ext/composio/oauth` route. That route then asks Composio for the real consent link and redirects the user there.

After the user finishes, Composio sends the browser back to the same bridge route with a `connected_account_id`. The route forwards the browser to ufo's normal callback, pretending that account id is the OAuth `code`. Finally, `exchange` checks with Composio that this account really belongs to the current workspace's Composio user and matches the requested provider. Only then does ufo bind the account. The important safety idea is that the token never enters ufo; it stays in Composio, and ufo only stores the connected account identity.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL the user's browser should visit when starting a Composio-backed connection. It deliberately points to ufo's own bridge route, not directly to Composio, because the real Composio consent link must be created asynchronously later.

**Data flow**: It receives a sealed `state` value, which preserves who and what this connection is for, and a `redirect_uri`, which is ufo's final callback address. It packages the provider name, state, and callback into query parameters, extracts the web origin from the callback, and returns a bridge URL under `/ext/composio/oauth`.

**Call relations**: This is the first step in the provider flow. It relies on `_origin` to safely reuse the callback's scheme and host, then the browser later reaches `oauth_route`, which creates the real Composio consent link.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This completes the connection after the browser comes back with a Composio connected-account id. It verifies that the account id belongs to this workspace's expected Composio user and provider before returning the account ufo should bind.

**Data flow**: It receives the returned `code`, which in this bridge is actually a Composio connected-account id, plus the current workspace id. It builds the expected Composio user id for that workspace, asks the Composio client to fetch and verify the connected account, optionally asks for a human-friendly label, and returns an `OAuthAccount` containing the account id and label. It does not read or store any OAuth token.

**Call relations**: This runs after core ufo receives the callback that `oauth_route` redirected to. It uses the Composio client to confirm ownership and then hands a clean account record back to ufo's normal connection machinery.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge that handles both trips through the Composio connection flow. On the way out, it creates a Composio consent link; on the way back, it forwards the connected account id to ufo's normal callback.

**Data flow**: It reads query parameters from the incoming request. If `state` or `callback` is missing, it returns an error because the connection cannot be safely completed. If Composio has returned a `connected_account_id`, it redirects the browser to the callback with that id as the `code`. If Composio reports a status without an account id, it returns a clear failure message instead of restarting consent. If this is the starting visit, it reads the provider, builds a return URL back to itself, asks Composio for a connect link tied to the workspace user, and redirects the browser to that link.

**Call relations**: The browser reaches this route after `ComposioOAuthProvider.authorize_url` sends it here. The route calls `_origin` when it needs to build safe absolute URLs, calls the Composio client to create the hosted consent link, and finally redirects back into ufo's normal callback path so that `ComposioOAuthProvider.exchange` can finish the binding.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the scheme and host from a URL, such as `https://example.com`. It also rejects callback URLs that are not complete web URLs, because the bridge must know where it is safe to send the browser.

**Data flow**: It receives a URL string, parses it into parts, checks that it uses `http` or `https` and includes a host name, and returns just the origin portion. If the URL is missing those required pieces, it raises an error instead of building a bad redirect.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` use this helper when constructing bridge and return URLs. It is the shared guardrail that keeps those redirects based on a real web origin.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Provider Request Proxying
The proxy layer translates ordinary provider HTTP requests into Composio-brokered calls and returns normal-looking responses without exposing user tokens.

### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Composio is used here as a safe middleman for credentials. A connector may want to call a provider such as Google Sheets, but the connector is not allowed to hold the user's token directly. This file solves that by wrapping the request and sending it to Composio's proxy endpoint, where Composio adds the credential on the server side.

The main piece, ComposioProxyTransport, behaves like an httpx transport, meaning it is the part of an HTTP client that actually sends requests. When a connector sends a normal provider request, this transport reads the method, URL, headers, and body, removes unsafe or irrelevant headers such as authorization and content-length, and packages the rest into a JSON request for Composio. The original query string stays in the URL so repeated query names still work, which matters for APIs that accept the same parameter name many times.

When Composio replies, the transport rebuilds a provider-style response: status code, headers, and body. If Composio says the provider returned binary data, the file does not pull those bytes through the proxy. Instead, it returns a redirect to a temporary file-store URL, like handing someone a pickup slip rather than carrying a heavy box through a narrow hallway.

ComposioRequestForwarder is the companion used by the broker forwarding path. It applies the same proxying behavior, but adds response-size and time limits so a slow or huge provider response cannot tie up the shared proxy process.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 66–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request rewrite step. It takes an ordinary provider HTTP request and sends it to Composio's proxy-execute endpoint so Composio can attach the hidden credential and call the provider safely.

**Data flow**: It starts with an incoming httpx request containing a method, URL, headers, optional body, and possibly a timeout. It reads the body, copies safe headers into Composio's expected parameter format, keeps the full provider URL including its query string, and builds a new POST request to Composio. After the inner transport returns Composio's response, it reads that response with a size limit if one is configured. If Composio itself failed, it returns that failure as an HTTP response. Otherwise, it parses the JSON reply and turns it into a provider-like response.

**Call relations**: This is called by httpx when ComposioProxyTransport is being used as the transport for a client, and it is also used directly by ComposioRequestForwarder.forward. During the flow it hands Composio's raw response body to ComposioProxyTransport._read_bounded so large replies do not grow without control, then hands the parsed successful payload to ComposioProxyTransport._provider_response so callers receive the shape of response they expected from the original provider.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response body from Composio, with an optional maximum size. The limit protects shared proxy workers from buffering very large responses in memory.

**Data flow**: It receives an httpx response from Composio. If no maximum size is set, it simply reads and returns the whole body. If a maximum is set, it reads the body chunk by chunk, adds the chunks to a buffer, and checks the total size as it goes. If the body grows past the allowed size, it closes the response and raises a ComposioError instead of continuing to store more bytes.

**Call relations**: ComposioProxyTransport.handle_async_request calls this right after Composio replies. It sits between the network response and the later response reconstruction step, making sure the system has the bytes it needs without allowing an unexpectedly large broker response to fill memory.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio's proxy-execute JSON format back into a normal-looking provider HTTP response. Callers can then use status codes, headers, and body data as if they had called the provider directly.

**Data flow**: It receives a parsed JSON dictionary from Composio plus the original request. It unwraps nested data envelopes until it reaches the actual provider result, reads the provider status and headers, and removes body-related headers that may no longer be accurate. If the payload points to binary data in Composio's file store, it returns a 302 redirect with a location header pointing to that file. Otherwise, it serializes the returned data into bytes, choosing JSON bytes for dictionaries or lists, plain UTF-8 bytes for strings, and an empty body when there is no data.

**Call relations**: ComposioProxyTransport.handle_async_request calls this after it has received and parsed a successful proxy response. This function is the final translation layer: it hides Composio's wrapper format and hands the rest of the code an ordinary httpx.Response. If Composio reports binary data without a usable URL, it raises a ComposioError because the caller would have no way to fetch the bytes.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport used to talk to Composio. It is used when the proxy transport is finished so network resources are not left open.

**Data flow**: It receives no new request data. It calls close on the inner transport, which releases any connections or transport resources held underneath. It returns nothing.

**Call relations**: ComposioRequestForwarder.forward calls this in its cleanup path after a forwarded request finishes or fails. It keeps the lifetime of the wrapped transport tidy, especially when the forwarder creates a temporary transport for one request.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a Composio-granted command-line or broker credential. It is a safe bridge from the broker forwarding path to the same proxy transport used by normal connector HTTP clients.

**Data flow**: It receives an account id, HTTP method, target URL, headers, and raw body bytes. It gets the configured Composio client, builds a ComposioProxyTransport for the requested connected account, and creates an httpx request with a timeout attached. It then runs the request through the transport under an overall deadline, reads the response body, closes the transport, and returns a ForwardedResponse containing the status, headers, and body. If the whole operation takes too long, it raises a ComposioError with a gateway-timeout style status.

**Call relations**: This is used by the broker forward path when a request must be executed with a Composio account credential. Rather than duplicating the proxy rewrite rules, it constructs ComposioProxyTransport and calls ComposioProxyTransport.handle_async_request directly. It wraps that lower-level transport behavior with practical broker safeguards: a maximum response size, a wall-clock timeout, and cleanup through ComposioProxyTransport.aclose.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).

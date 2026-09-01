# Composio Connector Broker and Dynamic Providers  `stage-10.2.1`

This stage is shared behind-the-scenes support for using Composio, an external service that connects users to many third-party apps and runs their tools. It sits between the project’s normal connector system and Composio’s hosted login, tool catalog, and tool execution features.

The client is the main doorway to Composio. It creates connection links, checks whether an account is connected, searches available tools, uploads files, and runs tools. The provider adapts the project’s usual sign-in flow to Composio’s hosted, sometimes delayed account-connection process. The broker is the central switchboard: other parts of the system ask it to discover tools, execute them, manage files, or send safe provider requests. The resolver lets the system use Composio toolkits by name, without registering each one in advance, while still checking that the requested provider is allowed. The proxy turns normal web requests into Composio proxy calls, so secret provider tokens stay hidden. The MCP session makes a single tool-router call over HTTP and returns a simple dictionary. The package file only makes these pieces importable.

## Files in this stage

### Broker façade
The broker exposes Composio discovery, execution, file, and proxy operations to the rest of the connector system.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

Think of this file as a front desk for Composio-backed connectors. The rest of the project asks for simple connector actions, such as “show me tools for Gmail,” “run this tool,” or “give me a safe way to call this account.” This broker translates those requests into Composio API calls and turns Composio’s answers back into the project’s own common shapes.

A key safety idea here is that the broker checks which workspace user owns a connected account before it returns a credential. The returned Credential does not contain the provider’s secret token. Instead, it uses a proxy transport that sends requests through Composio. That means code can sync data from a provider without ever holding the raw secret.

The file also smooths over common failure cases. If a tool slug is wrong, it tries to add a helpful list of real available tool slugs to the error. If an account is stale or no longer belongs to this broker, it tells the agent to ask the member to reconnect instead of pretending the tool was simply missing.

Files get special treatment too. Tool results may contain file objects anywhere inside a nested response, so the broker searches through the whole response to find downloadable files. For uploads, it asks Composio for a temporary upload slot and returns the exact argument shape that the tool expects.

#### Function details

##### `ComposioBroker.tools`  (lines 49–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Composio tools for a provider, optionally narrowed by a search query, and returns them in the project’s standard tool format. This is used when the system wants to discover what actions are available before choosing one.

**Data flow**: It receives a workspace ID, a provider name, and a search query. It asks the current Composio client for matching tools, then converts Composio’s raw list into BrokerTool objects. It returns a tuple of cleaned-up tool descriptions, slugs, input schemas, and read-only hints.

**Call relations**: When discovery is needed, this method fetches the active Composio client for that call and hands the raw rows to _discovered_tools so the rest of the system does not have to understand Composio’s listing format.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–66)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Gets the detailed input schema for one specific tool slug. A schema is the description of what arguments a tool accepts, like a form that says which fields are needed.

**Data flow**: It receives a workspace ID, provider, and tool slug. It asks Composio for that tool’s schema, rewrites file-upload fields into the project’s workspace-file format, checks whether the tool is marked read-only, and returns a BrokerTool. If Composio says the slug does not exist, it raises UnknownBrokerTool.

**Call relations**: This is called when the system already has a candidate tool and needs to know exactly how to call it. It relies on workspace_file_schema to normalize file inputs and _read_only to preserve Composio’s read-only signal.

*Call graph*: calls 1 internal fn (_read_only); 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 68–91)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for a workspace and connected account. This is the method that turns a chosen tool plus arguments into an actual action on the external service.

**Data flow**: It receives the workspace ID, provider, tool slug, arguments, connected account ID, and optional idempotency key. It builds the Composio external user ID from the workspace, sends the execution request, and returns Composio’s response dictionary. If execution fails because the account is stale, it changes the error into reconnect guidance; if the slug is missing, it tries to enrich the error with real available slugs.

**Call relations**: This is the main runtime path after a tool has been selected. It calls _stale_account to tell account failures apart from missing tools, _reconnect_error to produce useful reconnect advice, and _slug_miss to make wrong-tool errors more helpful.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 93–98)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a Composio tool response. Tool output can be deeply nested, so this gives the rest of the system one simple list of downloadable files.

**Data flow**: It receives the response dictionary from a tool execution. It creates an empty list, asks _collect_files to walk through the entire response, and returns any found files as BrokerFile objects in a tuple.

**Call relations**: After execute returns, callers can pass the response here to extract file attachments. The detailed recursive search is delegated to _collect_files so this public method stays simple.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 100–116)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a place to upload a file that a Composio tool will later use as input. It is like reserving a temporary drop box before handing its reference to the tool.

**Data flow**: It receives workspace, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL to PUT the file to, the content type to use, and the argument object that should be passed into the tool.

**Call relations**: This is used before executing tools that need file inputs. It relies on Composio to create the actual storage location and returns the project’s standard staged-upload wrapper.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 118–121)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for connector tools using Composio’s Tool Router, which is a search service for finding relevant tools by intent or query. This helps the system choose useful tools without listing everything manually.

**Data flow**: It receives workspace ID, provider, and query. It gets the current Composio client and passes all the search information to search_connector_tools. It returns a BrokerSearch result.

**Call relations**: This is another discovery path, separate from the direct tools listing. It acts mostly as a thin bridge from the broker interface to the Composio search helper.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 123–139)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a safe credential object for a connected account, after confirming that the account belongs to this workspace’s broker user. The credential does not expose the provider’s secret token; it routes requests through Composio’s proxy instead.

**Data flow**: It receives workspace ID, provider, and connected account ID. It builds the workspace’s broker-user identifier, asks Composio to verify the account, and if that succeeds returns a Credential using ComposioProxyTransport. If the account is not found for this workspace, it raises GrantUnusable with reconnect guidance.

**Call relations**: This is used when code needs to make provider HTTP requests through an existing grant. It checks ownership first to avoid a confused-deputy problem, where one workspace might accidentally use another workspace’s account.

*Call graph*: calls 1 internal fn (__init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, composio_client).


##### `ComposioBroker._slug_miss`  (lines 141–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by trying to include real tool slugs that are available for the provider. This helps the next attempt use a valid tool name instead of repeating the same mistake.

**Data flow**: It receives a Composio client, provider, missing slug, and original error. It turns the bad slug into a search query, asks Composio for similar tools, and if needed falls back to listing tools with an empty query. If it finds tools, it returns a new ComposioError whose message includes available slugs; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a 404 for execution and the failure was not a stale account. It uses _discovered_tools so the suggested alternatives are in the same cleaned-up format used by discovery.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through a nested tool response and picks out objects that look like Composio file outputs. It is needed because files may appear at any depth inside dictionaries or lists.

**Data flow**: It receives any value and a list being filled with found files. If the value is a file-shaped dictionary with a non-empty s3url, name, and mimetype, it appends a BrokerFile. If the value is a dictionary or list, it recursively checks each child. It does not return a value; it changes the found list in place.

**Call relations**: ComposioBroker.file_outputs starts the search and passes in the top-level response. This helper does the recursive walking and creates BrokerFile objects when it finds matching file records.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error likely means the connected account is gone or no longer usable. This matters because a dead account should lead to reconnect guidance, not a misleading list of tool names.

**Data flow**: It receives a Composio error and the account ID that was used. It lowercases the error message and looks for narrow signs that Composio could not find the connected account, either by wording or by the account ID itself. It returns true if the error looks like a stale account, otherwise false.

**Call relations**: ComposioBroker.execute calls this before treating a 404 as a missing tool. That ordering keeps account problems separate from slug problems.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a clearer Composio error that tells the caller the member should reconnect the provider account. It preserves the original status while adding human-actionable guidance.

**Data flow**: It receives the original Composio error and provider name. It combines the original error body with stale-grant guidance for that provider and returns a new ComposioError with the same status code.

**Call relations**: ComposioBroker.execute calls this after _stale_account says the account is probably dead or missing. The new error is then raised instead of continuing down the missing-tool path.

*Call graph*: called by 1 (execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 195–216)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio’s raw tool-list rows into the project’s standard BrokerTool objects. It filters out unusable entries and trims long descriptions so discovery results stay compact.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses a slug from the slug or name field, skips rows without a valid slug, rewrites input parameters into the workspace-file-aware schema, checks read-only tags, and builds a BrokerTool. It returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal discovery results, and ComposioBroker._slug_miss uses it when preparing helpful suggestions after a missing slug. It calls _read_only to preserve Composio’s safety hint.

*Call graph*: calls 1 internal fn (_read_only); called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


##### `_read_only`  (lines 219–221)

```
def _read_only(payload: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Composio tool is marked as read-only, meaning it is intended to inspect data rather than change it. This gives higher layers a useful safety signal.

**Data flow**: It receives a tool payload dictionary. It looks at the tags field and returns true only if it is a list containing the readOnlyHint tag. Otherwise it returns false.

**Call relations**: ComposioBroker.schema calls this for one detailed tool, and _discovered_tools calls it while converting listed tools. The result becomes the read_only field on BrokerTool.

*Call graph*: called by 2 (schema, _discovered_tools).


### Dynamic provider resolution
The package and resolver allow Composio-backed providers to be addressed by name and routed through shared integration machinery.

### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect request handling`

Composio offers many third-party toolkits, far too many to list one by one inside this project. This file solves that by creating an “open namespace”: if a user asks for a provider name, this resolver can decide whether Composio knows about it and whether the system may connect to it.

The main piece is `ComposioResolver`, a small, mostly stateless object. It keeps only one thing: a shared connector broker, which is the part of the system that actually performs brokered tool execution. When asked about a provider slug, meaning a short service name such as an identifier in a catalog, the resolver first rejects locally banned names. If the name is not banned, it asks Composio’s live catalog whether that toolkit is connectable.

If the provider is valid, the resolver can build an OAuth description. OAuth is the common web sign-in permission flow; here the token stays with Composio, so the local system does not need a provider host of its own. It can also create a connector entry, which is the system’s internal record saying “this provider exists, display it with this label, and send work to this broker.”

It also exposes Composio’s allowed file-transfer hosts, so files used by remote tools can still pass through the sandbox safely. Without this file, the system would only know about explicitly registered connectors and could not discover or connect arbitrary Composio toolkits by slug.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-store hosts are trusted for moving files in and out of brokered tools. It matters because remote tools may need file inputs or produce file outputs, and the sandbox needs to know which hosts are allowed.

**Data flow**: It reads no caller-provided input. It returns the fixed list of Composio transfer host names from the Composio client module, without changing anything.

**Call relations**: When the connector system needs to know what external file locations are allowed for this resolver, it asks this property. The property simply hands back Composio’s shared allow-list so the sandbox can permit those file transfers while still blocking unknown hosts.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function answers the question, “Can this resolver take responsibility for this provider name?” It rejects names that are banned locally, then checks Composio’s live catalog to see whether the requested toolkit can actually be connected.

**Data flow**: It receives a provider name as text. First it lowercases the name and compares it with the local banned list; if it is banned, the result is `False`. Otherwise it creates or fetches the Composio client, asks whether that provider is a connectable toolkit, and returns `True` only when Composio confirms it exists and can be connected.

**Call relations**: The wider connector registry uses this during provider resolution, after explicitly registered connectors have had a chance to claim the name. If no fixed connector claims it, this resolver checks Composio by calling `ufo_ext_composio.client.composio_client` and then asking that client about the toolkit.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth connection description for a validated Composio provider. OAuth is the permission flow where a user grants access; in this case Composio keeps the actual account token and runs tools server-side.

**Data flow**: It receives a provider name. It creates and returns a `ComposioOAuthProvider` for that provider, with an empty host because the local system is not talking directly to the third-party provider’s own server.

**Call relations**: After the resolver has accepted a provider name, the connect flow can ask for this descriptor so it knows what kind of authorization route to present. The function hands off to `ComposioOAuthProvider.__init__` to create the provider-specific OAuth description.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the system’s internal connector entry for a Composio toolkit. The entry gives the provider a readable label and points execution at the shared Composio broker.

**Data flow**: It receives a provider slug such as a short catalog name. It turns underscores into spaces and title-cases the result for display, combines that with the original provider name and the resolver’s broker, and returns a new `ConnectorEntry`.

**Call relations**: Once a Composio provider has been accepted, the connector system uses this to register the provider for actual use. It calls `ConnectorEntry.__init__` to package the provider name, human-friendly label, and shared broker together.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–51)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT, after: str | None=None) -> CatalogPage
```

**Purpose**: This function searches Composio’s toolkit catalog so users or discovery tools can find connectable services. It supports paging, so callers can ask for the next batch of results instead of loading everything at once.

**Data flow**: It receives a search query, a maximum number of results, and optionally an `after` marker that means “continue after this previous result.” It gets the current Composio client, asks that client to list matching toolkits, and returns the catalog page it receives.

**Call relations**: Discovery features call this when they need to show possible Composio toolkits. The function delegates the real network/catalog work to the client returned by `ufo_ext_composio.client.composio_client`, so the resolver stays focused on routing and policy rather than catalog transport details.

*Call graph*: 1 external calls (composio_client).


### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project can then refer to this extension by its package path, such as importing modules that live under `ufo_ext_composio`. Think of it like a label on a folder in a filing cabinet: the label does not contain the documents, but it makes the folder recognizable and usable by the system. Since this file has no code, it does not run setup steps, expose helper functions, or change any settings. Its value is structural: without it, depending on the Python version and packaging setup, imports or extension discovery might not work as expected.


### Composio service access
The client and MCP session helpers perform direct Composio service operations such as account checks, tool lookup, file upload, tool execution, and Tool Router calls.

### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling and connector tool discovery/execution`

Composio acts like a secure switchboard for third-party services such as GitHub and many others. Instead of this project storing a separate secret token for every connected app, Composio keeps the token and this client stores only a connected-account id. Without this file, the connector system could not safely send users through OAuth consent, discover which Composio tools exist, or ask Composio to run those tools.

The file has three main jobs. First, it decides which Composio toolkits are safe and useful to offer. A toolkit must have Composio-managed sign-in support, must actually contain tools, and must not be on the project’s manually banned list. Second, `ComposioClient` wraps Composio’s REST API, which is the ordinary request-and-response web API exposed by Composio. It opens short-lived HTTP clients, sends authenticated requests, checks that replies have the expected shape, and turns bad replies into loud errors. Third, it adapts Composio’s tool search results into the project’s connector format, including rewriting file inputs so the agent only sees workspace file paths, not Composio’s internal upload storage details.

One important safety theme runs through the file: member-supplied names are checked before being put into URLs, accounts are checked for owner and toolkit before use, and large tool execution payloads are rejected before sending.

#### Function details

##### `connectable`  (lines 130–154)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit should be offered to users by this deployment. It filters out known-bad toolkits, toolkits without managed sign-in support, and toolkits that list no usable tools.

**Data flow**: It receives a toolkit slug, which is the toolkit’s short name, and a catalog record from Composio. It checks the local banned list, looks for managed authentication schemes, and confirms the catalog says there is at least one tool. It returns `True` only when all those checks pass.

**Call relations**: This is the gatekeeper used when checking a single toolkit in `ComposioClient.connectable_toolkit` and when building catalog pages in `ComposioClient.list_toolkits`. Those callers use its yes-or-no answer before exposing a toolkit to the rest of the connector system.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 161–164)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Builds a clear error when Composio rejects a request or returns data this client cannot safely use. It keeps both the HTTP status number and the response body for later diagnosis.

**Data flow**: It receives a status code and a text body. It formats them into a readable exception message and stores the raw pieces on the error object. The output is an exception ready to be raised.

**Call relations**: Many client methods raise this when a Composio response is missing important fields or points to the wrong owner or toolkit. `_body` also raises it when any HTTP response is an error or is not a JSON object.

*Call graph*: called by 6 (_account, _auth_config, connect_link, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 181–190)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the hosted sign-in link a user opens to connect an external service through Composio. This is the start of the consent flow, similar to sending someone to a bank’s website before your app can read their transactions.

**Data flow**: It takes a toolkit name, a broker user id, and a callback URL. It first finds or creates the right Composio authentication configuration, then posts a link request to Composio. It returns the redirect URL, or raises an error if Composio does not provide one.

**Call relations**: It relies on `_auth_config` to choose the sign-in configuration and `_post` to call Composio. If the reply is malformed, it raises `ComposioError` so the caller does not continue with a broken connection flow.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 192–196)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a connected account id is valid for the expected user and toolkit, then wraps it in the project’s standard OAuth account object. This prevents accidentally using another user’s or another service’s connection.

**Data flow**: It receives an account id, the expected owner id, and the expected toolkit. It asks `_account` to verify the Composio account record. If the checks pass, it returns an `OAuthAccount` containing the account id.

**Call relations**: This is a public verification step that delegates the real checking to `_account`. The returned `OAuthAccount` is what the rest of the connector framework can store and pass around instead of a secret token.

*Call graph*: calls 1 internal fn (_account); 1 external calls (__init__).


##### `ComposioClient._account`  (lines 198–227)

```
async def _account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> dict[str, object]
```

**Purpose**: Fetches a connected account from Composio and checks that it is safe to use. It verifies ownership, active status, and that the account belongs to the requested toolkit.

**Data flow**: It receives an account id plus the expected user and toolkit. It reads the account record from Composio, compares the recorded owner with the expected owner, checks that the status is `ACTIVE`, and compares the toolkit slug. It returns the raw account payload if everything matches, or raises an error if not.

**Call relations**: `connected_account` calls this before creating an `OAuthAccount`. It uses `_get` to read Composio and raises either `ComposioError` for wrong owner/toolkit or `GrantUnusable` when the user needs to reconnect an inactive grant.

*Call graph*: calls 3 internal fn (__init__, _get, __init__); called by 1 (connected_account).


##### `ComposioClient.account_label`  (lines 229–232)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a human-friendly label for a connected account, if Composio has one. This can be used to show a user which account they connected.

**Data flow**: It receives a connected account id and fetches that account from Composio. It reads the `alias` field and returns it only if it is a non-empty string. If no useful alias exists, it returns `None`.

**Call relations**: It uses `_get` for the API request. Unlike `_account`, it does not verify ownership or toolkit; it is a small lookup for display information.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 234–264)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the tools Composio offers for one toolkit, optionally narrowed by a search query. It follows Composio’s pages so tools beyond the first page are not invisible.

**Data flow**: It receives a toolkit slug and an optional query. It repeatedly asks Composio for pages of tool rows, keeps only dictionary-shaped rows, follows the next-page cursor, and stops at the end or at the project’s maximum listing size. It returns a tuple of tool records.

**Call relations**: The broker’s slug-miss path calls this when it needs to discover possible tools for a connector. Internally it uses `_get` for each page request.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 266–267)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full catalog record for one specific Composio tool. A caller uses this when it needs the details of a known tool slug.

**Data flow**: It receives a tool slug, sends a GET request for that tool, and returns Composio’s JSON object as a Python dictionary.

**Call relations**: This is a thin public wrapper over `_get`. It leaves response validation to `_body`, which is reached through `_get`.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 269–286)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a user-supplied toolkit slug names a toolkit this deployment is willing to broker, and returns its display name if so. It also blocks unsafe slug characters before they can become part of a URL.

**Data flow**: It receives a slug string. It first checks the slug contains only allowed identifier characters, then fetches the toolkit record from Composio. If the toolkit is missing or fails `connectable`, it returns `None`; otherwise it returns Composio’s name or the slug as a fallback.

**Call relations**: It uses `_get` to read one toolkit and `connectable` to apply the project’s offerability rules. A 404 from Composio becomes a simple `None`, while other Composio errors are passed upward.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 288–315)

```
async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads one page of Composio toolkits that this deployment can offer to members. It turns Composio’s raw catalog rows into the project’s smaller catalog-entry format.

**Data flow**: It receives a search query, a page size, and an optional cursor for continuing a previous listing. It fetches a catalog page, skips malformed or non-connectable items, and creates entries with provider slugs and display labels. It returns a `CatalogPage` with entries and the next cursor, if any.

**Call relations**: It calls `_get` to read Composio’s toolkit catalog and `connectable` to filter each row. It hands the cleaned result back as `CatalogEntry` and `CatalogPage` objects used by the connector catalog flow.

*Call graph*: calls 2 internal fn (_get, connectable); 2 external calls (__init__, __init__).


##### `ComposioClient.execute_tool`  (lines 317–331)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Asks Composio to run one tool on its server side. This is where actual connector work happens, while Composio injects the user’s stored token without giving that token to this project.

**Data flow**: It receives a tool slug, arguments, a broker user id, and optionally a connected account id and idempotency key. It builds the request body, rejects it if the serialized body is over the size limit, adds the idempotency header if present, and posts to Composio. It returns Composio’s result object.

**Call relations**: It uses `_post` for the execute API call and `json.dumps` to measure the outgoing payload size. The idempotency key helps callers safely retry an operation without accidentally doing it twice, when Composio supports that behavior.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 333–357)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file that will be used as a tool argument. It gives the caller a storage key for the tool input and, when needed, a temporary upload URL.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts those details to Composio’s upload-request endpoint. It returns a `ComposioUpload` with the file key and either a presigned PUT URL or `None` if Composio says the bytes already exist.

**Call relations**: It uses `_post` to request the upload slot. If Composio omits the required key or sends a bad URL shape, it raises `ComposioError` rather than letting later tool execution fail mysteriously.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 359–370)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The Tool Router is Composio’s helper service that can search for tools by intended use instead of exact slug.

**Data flow**: It receives a broker user id and a list of toolkits to enable. It posts a session request to Composio, reads the session id and MCP URL from the reply, and returns them as a `ToolRouterSession`. If either required value is missing, it raises an error.

**Call relations**: `search_connector_tools` calls this when no cached search session exists for a user and connector. It uses `_post` for the session request and returns the endpoint later used for the MCP tool call.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 372–390)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration that should be used for a toolkit’s OAuth consent flow, creating a Composio-managed one if the project does not already have one. This lets operator-created custom configs take priority.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts the first id if present. If none exists, it posts a request to create a managed auth config and returns the new id. If the create response has no id, it raises `ComposioError`.

**Call relations**: `connect_link` calls this before creating a user-facing connection link. It uses `_get`, `_post`, and `_auth_config_id` to keep that higher-level method focused on the consent link itself.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 392–394)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends an authenticated GET request to Composio and returns a checked JSON object. A GET request is a read-only web request.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the GET request, passes the response to `_body`, and returns the parsed dictionary.

**Call relations**: All read-style methods in `ComposioClient` call this, including account lookup, auth config lookup, toolkit listing, tool listing, and schema lookup. It uses `_http` to build the client and `_body` to enforce response rules.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_account, _auth_config, account_label, connectable_toolkit, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 396–400)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends an authenticated POST request to Composio and returns a checked JSON object. A POST request is used here when asking Composio to create something or run an action.

**Data flow**: It receives an API path, a JSON body, and optional extra headers. It opens an HTTP client, sends the POST request, passes the response through `_body`, and returns the parsed dictionary.

**Call relations**: Creation and action methods call this, including auth config creation, connection link creation, tool execution, file upload preparation, and Tool Router session creation. It uses `_http` for the network client and `_body` for validation.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 402–408)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Creates the short-lived HTTP client used for one Composio request. It sets the base URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the client’s API key and optional transport. It builds an `httpx.AsyncClient`, which is an asynchronous web client that can make requests without blocking the whole program. The caller uses it in a context that closes it after the request.

**Call relations**: `_get` and `_post` call this whenever they need to talk to Composio. Keeping client creation here makes every request consistently authenticated and time-limited.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 411–419)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Composio into a safe Python dictionary or a clear error. It is the common response checker for this client.

**Data flow**: It receives an HTTP response. If the status code signals failure, it raises `ComposioError` with the response text. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and requires the result to be an object; non-object replies become errors.

**Call relations**: `_get` and `_post` send every Composio response through this function. That means higher-level methods can assume they receive a dictionary or an exception, not half-parsed or unexpected data.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 422–448)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio tool input schemas so file uploads are described in the project’s own terms: a path inside `/workspace`. This hides Composio’s internal file-store fields from the agent.

**Data flow**: It receives any schema-shaped value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object requiring `workspace_file`. For nested dictionaries and lists, it walks through them and rewrites any uploadable parts. Other values are returned unchanged.

**Call relations**: `_search_result` calls this while converting Tool Router results into broker tools. The result is what the model sees when deciding how to call a file-using tool.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 451–458)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio listing response. It is a small helper for the consent setup path.

**Data flow**: It receives a response dictionary. It looks for an `items` list and returns the first item’s string `id` if present. If the expected shape is not there, it returns `None`.

**Call relations**: `ComposioClient._auth_config` calls this after listing existing auth configs. A returned id lets `_auth_config` reuse an existing setup instead of creating a new managed one.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 461–468)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the deployment’s default `ComposioClient` from the `COMPOSIO_API_KEY` environment variable. It fails immediately if the key is missing.

**Data flow**: It reads the process environment for `COMPOSIO_API_KEY`. If the value is absent or empty, it raises a runtime error. Otherwise it returns a new `ComposioClient` with that key.

**Call relations**: This factory is used by code that needs the standard Composio client for the deployment. By failing loudly, it prevents OAuth and connector exchange flows from pretending to work without broker credentials.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 475–476)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely turns an unknown value into a dictionary only when it already is one. It avoids repeated type checks in result parsing.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: `_search_result` uses this repeatedly while reading Tool Router data that may be missing or oddly shaped. This keeps malformed sections from crashing simple field extraction.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 479–482)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts a tuple of non-empty strings from a list-like value. It is used when Tool Router fields should contain lists of text.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only non-empty strings and returns them as a tuple.

**Call relations**: `_search_result` uses this for tool slug lists, plan steps, guidance, and pitfalls. That gives the broker clean text collections even when Composio’s response contains extra or invalid values.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 485–519)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router search output into the project’s `BrokerSearch` format. It gathers matched tools, their input schemas, suggested plan steps, guidance, and known pitfalls.

**Data flow**: It receives the raw Tool Router result dictionary. It finds the nested data and tool schemas, walks each search result, collects primary and related tool slugs without duplicates, rewrites file-upload schemas into workspace-file schemas, and gathers planning notes. It returns a `BrokerSearch` object.

**Call relations**: `search_connector_tools` calls this after receiving the MCP tool-call result. It uses `_dict`, `_str_tuple`, and `workspace_file_schema`, then creates `BrokerTool` objects that the dynamic connector tools can present to the model.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 522–545)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Performs semantic search for useful Composio tools for one workspace and connector. Instead of searching only exact names, it asks Composio’s Tool Router which tools fit the user’s described use case.

**Data flow**: It receives a `ComposioClient`, workspace id, connector slug, and search query. It builds the broker user id, reuses a cached Tool Router session when possible, or creates one under a lock so concurrent searches do not open duplicates. It calls the Tool Router MCP endpoint with the query and converts the result into `BrokerSearch`.

**Call relations**: It calls `ComposioClient.tool_router_session` when a cached session is missing, then calls `mcp_session.mcp_call_tool` to run Composio’s search tool. Finally it hands the raw result to `_search_result` so callers receive the project’s standard search shape.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio's Tool Router. Composio exposes tool search through an MCP endpoint. MCP, or Model Context Protocol, is a standard way for an app to talk to external tools. Here, the file opens a short-lived HTTP-based MCP connection, calls exactly one tool, then closes the connection.

The main job is not to execute Composio tools. It is only for search through the Tool Router, especially the `COMPOSIO_SEARCH_TOOLS` tool. Actual tool execution happens elsewhere through Composio's execute API, which keeps billing and permissions tied to the right Composio grant.

The response from an MCP tool can come back in a few shapes. It might already contain structured data, like a ready-made dictionary. It might contain separate structured content. Or it might contain text that is actually JSON. This file checks those options in order and converts the first usable result into a normal dictionary. If it only gets plain text, it wraps that text in a dictionary so callers still receive a predictable shape.

A useful detail for tests is that `Client` is imported as a module-level name. Tests can replace it with a fake client, like swapping a real phone line for a toy one, so no live Composio endpoint is needed.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one MCP tool at a given HTTP endpoint and returns the result as a plain dictionary. It is used when the project needs to ask Composio's Tool Router for structured information, such as tool search results, without exposing the rest of the code to MCP connection details.

**Data flow**: It receives an endpoint URL, a tool name, tool arguments, HTTP headers, and a timeout. It opens a streamable HTTP MCP connection with those headers, calls the named tool with a copy of the arguments, and waits up to the timeout. After the call finishes, it looks for a dictionary in the response data first, then in structured content, then tries to parse the first text response as JSON. It returns a dictionary in all normal cases, wrapping plain text or non-dictionary JSON if needed.

**Call relations**: This function is the file's single bridge into the external MCP world. When another part of the extension needs one Tool Router call, it comes here; this function creates the `StreamableHttpTransport`, uses the FastMCP `Client` to call the tool, and uses `json.loads` only if the response arrives as text that may contain JSON.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### Hosted connections and proxying
The provider bridge and proxy layer adapt ufo OAuth-style flows and provider API requests to Composio-hosted connections without exposing provider tokens.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `connect flow / browser OAuth callback handling`

OAuth is the common “sign in and allow access” flow used by many services. ufo expects that flow to start with a ready-made authorization URL and later finish by exchanging a returned code for an account grant. Composio works differently: ufo must first make an async API call to Composio to create a temporary connect link. This file hides that mismatch.

The main idea is a browser bridge. Instead of sending the user's browser straight to Composio, `ComposioOAuthProvider.authorize_url` sends it to this extension's own `/ext/composio/oauth` route. That route can do the async work: it asks Composio for a hosted consent link, tied to the current workspace, then redirects the browser there.

When Composio is done, it sends the browser back to the same route with a `connected_account_id`. The route then forwards the browser to ufo core's normal callback, placing that account id where ufo expects an OAuth `code`. Finally, `ComposioOAuthProvider.exchange` checks with Composio that the account really belongs to this workspace and provider before returning an `OAuthAccount`. The actual secret token stays inside Composio, so this extension never reads or stores it.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first URL that the user's browser should visit when starting a Composio-backed connection. Instead of pointing directly at Composio, it points at this extension's local OAuth bridge route so the extension can create the real Composio consent link.

**Data flow**: It receives the sealed `state` value from ufo core and the final `redirect_uri` that core wants to receive later. It packages the provider name, state, and callback into query parameters, finds the scheme and host from the callback URL, and returns a URL under `/ext/composio/oauth`. Nothing is saved or changed here; it only produces the next browser destination.

**Call relations**: This is the first step of the provider's connection story. It uses `_origin` to keep the bridge URL on the same web origin as the callback, and `urlencode` to safely place values into the query string. The URL it returns leads the browser into `oauth_route`, where the async Composio link creation can happen.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected-account id into ufo's standard `OAuthAccount` record. It also verifies that the account id belongs to the expected workspace-specific Composio user and to this provider, which helps prevent someone from injecting another account id into the callback.

**Data flow**: It receives `code`, which in this flow is really Composio's connected account id, plus the workspace id. It builds the expected Composio user id for that workspace, asks the Composio client to fetch and validate the connected account, then tries to fetch a human-friendly label for it. It returns an `OAuthAccount` containing the confirmed account id and, when available, the label. The provider token itself remains stored by Composio.

**Call relations**: This runs after the browser has returned through `oauth_route` and ufo core asks the provider to finish the connection. It calls `composio_client` to speak to Composio, then hands back an `OAuthAccount` in the shape ufo core expects.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge for both halves of the Composio consent trip. It starts the trip by creating a Composio connect link, and it finishes the trip by forwarding Composio's returned account id back to ufo core.

**Data flow**: It reads query parameters from the incoming request. If `state` or `callback` is missing, it returns a bad-request response because the connection cannot be safely tied back to the original user action. If Composio has returned a `connected_account_id`, it redirects to the core callback with that id as the `code`. If Composio returned a failure `status` without an account id, it shows a clear failure response instead of restarting the flow. Otherwise, it treats the request as the start leg: it reads the provider, builds a return URL back to itself, asks Composio for a connect link for the current workspace, and redirects the browser to that link.

**Call relations**: The URL made by `ComposioOAuthProvider.authorize_url` sends the browser here first. On the start leg, this route calls `_origin` and `composio_client().connect_link` so Composio can create the hosted consent page. On the return leg, it creates the redirect response that sends the browser onward to ufo core's normal callback, which will later cause `ComposioOAuthProvider.exchange` to validate and bind the account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts just the origin from a full URL: the scheme and host, such as `https://example.com`. This keeps bridge redirects anchored to the same web host as the callback URL.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme plus a host name. If the URL is valid, it returns only `scheme://host`. If it is missing those required parts, it raises an error because the OAuth bridge cannot safely build a redirect target from it.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` call this helper when they need to build a bridge URL from a callback URL. It delegates the low-level URL splitting to `urlparse`, then provides the small safety check this file needs.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Some connectors need to call outside services, but Composio keeps the real account credential on its own servers. That is safer, but it means the connector cannot simply attach a token and call the provider directly. This file is the adapter that bridges that gap.

The main piece, ComposioProxyTransport, acts like a special HTTP transport. A transport is the part of an HTTP client that actually sends the request. Instead of sending the request to the provider, it packages up the original method, URL, selected headers, and body, then sends them to Composio’s proxy endpoint. Composio adds the credential on the server side, calls the real provider, and returns the provider’s status, headers, and body.

The file is careful about what it forwards. It skips headers such as authorization, host, and content length because those belong to the local connection, not the provider request. It also preserves query strings in the URL so repeated query parameters still work.

Large or non-JSON responses need special care. If Composio stores binary data separately, this file returns a redirect to that stored file instead of pulling many megabytes through the shared proxy process. ComposioRequestForwarder uses the same transport for one-off broker forwarding, with size and time limits so a slow or huge response cannot tie up the service indefinitely.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 66–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request-rewriting step. It receives what looks like a normal provider HTTP request and sends an equivalent request to Composio’s proxy endpoint, so Composio can add the hidden account credential and contact the provider.

**Data flow**: It starts with an httpx request containing a method, URL, headers, body, and optional timeout. It reads the body, builds a JSON payload with the connected account id and the original request details, removes headers that should not be forwarded, and sends a POST request to Composio. It then reads Composio’s response with the file’s size-protection helper. If Composio itself returned an error, it returns that error as an HTTP response. Otherwise, it turns the proxy payload back into a provider-like response.

**Call relations**: This function is the front door of ComposioProxyTransport. During a provider call, it calls _read_bounded to safely collect Composio’s answer, then calls _provider_response to rebuild the response that the connector expects. ComposioRequestForwarder also drives this function when forwarding a single broker request.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response body from Composio, while optionally enforcing a maximum size. The limit protects the shared proxy process from buffering an unexpectedly huge response in memory.

**Data flow**: It receives an HTTP response from Composio. If no size limit is configured, it simply reads and returns all bytes. If a limit is set, it reads the response piece by piece, adds each piece to a buffer, and checks the total size. If the response grows beyond the limit, it closes the response and raises a Composio error instead of continuing to consume memory.

**Call relations**: handle_async_request calls this right after the proxy request returns. Its job is to make sure the response body is available for parsing, but only if doing so is safe under the configured size limit.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This converts Composio’s proxy response format back into a normal HTTP response from the provider’s point of view. It exists so the rest of the connector can keep working as if it had called the provider directly.

**Data flow**: It receives a decoded JSON payload from Composio and the original request. It unwraps nested data envelopes, reads the provider status and headers, removes headers that should be recalculated for the new body, and then builds the response content. If Composio reports binary data stored elsewhere, it returns a 302 redirect with a location header pointing to that stored file. If the data is JSON-like, text, missing, or another simple value, it encodes that into bytes and returns an httpx response.

**Call relations**: handle_async_request calls this after it has successfully received and decoded Composio’s proxy reply. This function is the last translation step before the caller receives a provider-shaped response.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport used to talk to Composio. Closing it frees network resources such as open connections.

**Data flow**: It has no input beyond the transport object itself. It asks the inner transport to close, and it returns nothing after cleanup is complete.

**Call relations**: This is used when the ComposioProxyTransport is no longer needed. ComposioRequestForwarder calls it in a cleanup step after a forwarded request finishes or fails, so connections are not left open.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This performs one provider request through Composio for the broker forwarding path. It is used when the proxy needs to forward a CLI-granted request without giving the caller the real provider credential.

**Data flow**: It receives an account id, HTTP method, URL, headers, and raw body bytes. It gets the configured Composio client, builds a ComposioProxyTransport with the account id and API key, creates a normal HTTP request, and runs it through the transport under a wall-clock timeout. It reads the response body, closes the transport, and returns a ForwardedResponse containing the status, headers, and body. If the whole operation takes too long, it raises a Composio timeout error.

**Call relations**: This is the one-shot forwarding wrapper around ComposioProxyTransport. It constructs the transport, calls handle_async_request to do the actual proxy rewrite and provider call, then packages the result into the broker’s ForwardedResponse format for the caller.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).

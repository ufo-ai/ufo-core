# Composio brokered tool integration  `stage-16.2`

This stage is behind-the-scenes support for using external app tools through Composio, a service that connects many apps through one doorway. It helps during setup, when a user connects an account, and during the main work loop, when the agent discovers and runs tools.

The package marker file simply makes this extension loadable by Python. The resolver turns a Composio slug, such as “github,” into connector details, so the system does not need a separate built-in entry for every app. The provider handles the browser consent step, similar to “sign in with Google,” where the user approves access without sharing passwords with UFO.

The client talks to Composio’s API to create consent links, check accounts, search tool catalogs, upload files, and run tools. The broker is the main adapter: it presents Composio tools in the shape UFO expects, including inputs, outputs, files, and safe authenticated calls. The MCP session makes a single remote Tool Router call and simplifies the reply. The proxy rewrites ordinary web requests so Composio adds the secret credential on its servers, then returns a normal response.

## Files in this stage

### Connector discovery and consent
Package setup, dynamic Composio toolkit resolution, and OAuth-style account connection support.

### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `package import`

This file is intentionally empty, but it still has a useful job. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. That means other parts of the system can refer to this folder using package-style imports, like addressing a room by name in a building directory. Without this file, some Python tools or older Python setups might not recognize `ufo_ext_composio` as a package, which could make imports fail or make packaging less predictable. Because the file contains no code, it does not run setup steps, expose shortcuts, or change behavior when the package is imported. Its role is structural: it helps the project layout work cleanly.


### `extensions/composio/ufo_ext_composio/resolver.py`

`domain_logic` · `connector discovery and connect setup`

Composio offers access to many outside services through one brokered platform. This file is the bridge that makes those services feel like normal connectors in this project, even though they are not individually listed in the local connector registry. Think of it like a hotel front desk: instead of keeping a separate key desk for every room, the system asks one shared desk, Composio, whether a room exists and how to reach it.

The main class, ComposioResolver, is small and deliberately stateless. It keeps only a shared connector broker, while looking up the Composio client fresh each time. That matters for tests and configuration changes, because the current client settings are respected instead of being accidentally cached.

When asked whether it can claim a provider name, it first rejects locally banned names, then checks Composio’s live catalog to see whether that toolkit can actually be connected. When a connection is allowed, it creates a simple OAuth provider description. OAuth is the common web sign-in flow where a user grants access without sharing their password. Here, the user’s account token stays with Composio, and tools run through Composio’s side rather than directly against the provider host.

The file also exposes a searchable catalog of connectable toolkits and declares Composio file-transfer hosts so tool inputs and outputs can pass safely through the sandbox.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-storage hosts are allowed for transferred files. It is used so files produced or consumed by Composio-backed tools can safely cross the sandbox boundary.

**Data flow**: It takes no outside input beyond the resolver object. It reads the fixed Composio transfer-host list and returns it unchanged as a tuple of host names.

**Call relations**: When the connector system needs to know which remote hosts are trusted for file movement, it asks this resolver. The answer is not built dynamically; it simply hands back the shared Composio host allow-list.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This checks whether a provider name should be treated as a Composio-backed toolkit. It prevents banned names from being accepted locally, then asks Composio whether the toolkit is actually connectable.

**Data flow**: A provider slug goes in. The function lowercases it for the ban check; if it is banned, the result is immediately false. Otherwise it gets the current Composio client, asks whether that toolkit can be connected, and returns true only if Composio finds it.

**Call relations**: During connector resolution, this is the gatekeeper for Composio’s open namespace. It calls on the Composio client only after the local ban list passes, so obviously disallowed names never reach the remote catalog check.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This builds the OAuth description for a Composio toolkit after the provider name has been accepted. The description tells the rest of the system how the connection should be represented.

**Data flow**: A provider slug goes in. The function wraps it in a ComposioOAuthProvider with an empty host, because the real service access is brokered through Composio rather than by storing a direct provider host here. The OAuth provider object comes out.

**Call relations**: Once the resolver has claimed a provider, the connection flow can ask for this descriptor. The function hands off to ComposioOAuthProvider to create the object used by the broader connector framework.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This creates the connector entry that routes a Composio toolkit to the shared Composio broker. It also turns the provider slug into a human-friendly label.

**Data flow**: A provider slug goes in. The function builds a display label by replacing underscores with spaces and title-casing the words. It returns a ConnectorEntry containing the original provider slug, the label, and this resolver’s shared broker.

**Call relations**: After a provider has been accepted, the connector registry can ask for an entry. This function packages the slug and broker together so later tool execution is sent through the common ComposioBroker rather than a separate connector for each toolkit.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–52)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: This searches Composio’s toolkit catalog and returns results the system can show during discovery. It helps users find services they can actually connect through Composio.

**Data flow**: A search query and optional limit go in. The function gets the current Composio client, asks it for matching toolkits, then converts each returned slug and label into a CatalogEntry. The result is a tuple of catalog entries.

**Call relations**: Discovery tools use this when they need searchable Composio options. It asks the Composio client for the live catalog, then hands back plain CatalogEntry objects that fit the project’s normal connector discovery format.

*Call graph*: 2 external calls (__init__, composio_client).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `connect/OAuth request handling`

UFO expects an OAuth provider to give it a web address where the user can approve access, then later exchange a returned code for an account. Composio works a little differently: creating the real consent link requires an asynchronous API call. This file solves that mismatch by sending the browser first to UFO's own extension route, then having that route ask Composio for the real link.

The flow is like a receptionist forwarding a visitor to the right office. `ComposioOAuthProvider.authorize_url` does not point directly to Composio. It points to this extension's `/ext/composio/oauth` route and includes the provider name, the security `state`, and the callback URL that UFO core expects. When `oauth_route` receives that first browser visit, it asks Composio to create a consent link for the current workspace's external user, then redirects the browser there.

After the user finishes, Composio sends the browser back to the same route with a `connected_account_id`. The route then forwards the browser to UFO core's callback, passing that account id as the OAuth `code`. Finally, `exchange` checks with Composio that the account really belongs to this workspace user and provider before returning an `OAuthAccount`. Tokens stay inside Composio; UFO stores only the connected account identity and optional label.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first browser URL for starting a Composio connection. Instead of sending the user directly to Composio, it sends them to this extension's OAuth bridge route so the async Composio link can be created there.

**Data flow**: It receives a sealed `state` value and a UFO callback URL. It reads this provider's Composio toolkit name, extracts the origin from the callback URL, adds the provider, state, and callback as query parameters, and returns a URL under `/ext/composio/oauth` for the browser to visit.

**Call relations**: UFO's connect flow calls this when it needs an authorization page. This function relies on `_origin` to keep the bridge on the same scheme and host as the callback, and on URL encoding so the state and callback travel safely through the browser redirect.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected account id into UFO's `OAuthAccount` record. It also verifies that the account id belongs to the expected workspace-scoped Composio user and the expected provider before UFO binds it.

**Data flow**: It receives the returned `code`, which in this flow is really a Composio connected account id, plus the workspace id. It builds the expected external user id for that workspace, asks the Composio client to fetch and validate the connected account, tries to fetch a friendly account label, and returns an `OAuthAccount` containing the account id and optional label. If label lookup fails, it still returns the account with no label.

**Call relations**: UFO core calls this after `oauth_route` has redirected back to the core callback with the account id as the code. This function hands the verification work to the Composio client, then hands UFO core a clean account object to store for later tool use.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Runs the browser bridge for both halves of the Composio consent flow. On the way out, it creates a Composio consent link and redirects the user there; on the way back, it forwards the connected account id to UFO core's callback.

**Data flow**: It reads query parameters from the incoming request: `state`, `callback`, possibly `provider`, possibly `connected_account_id`, and possibly `status`. If state or callback is missing, it returns a bad-request response. If an account id is present, it redirects to the callback with `state` and `code`. If Composio returned a status without an account id, it returns an error message instead of restarting consent. Otherwise, it treats the request as the start of consent, builds a return URL back to itself, asks Composio for a connect link for the current workspace user, and redirects the browser to that link.

**Call relations**: The URL produced by `ComposioOAuthProvider.authorize_url` sends the browser here first. Later, Composio sends the browser here again after consent. This route uses `_origin` to build safe same-origin bridge URLs, uses the Composio client to mint the hosted consent link, and returns HTTP redirects or error responses for the browser.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the scheme and host from a full URL, such as `https://example.com`. It is used to build bridge URLs on the same web origin as UFO's callback.

**Data flow**: It receives a URL string, parses it, checks that it has an `http` or `https` scheme and a host name, and returns only the scheme plus host. If the URL is not suitable for browser redirects, it raises an error instead of producing an unsafe or broken origin.

**Call relations**: `ComposioOAuthProvider.authorize_url` uses this when creating the initial bridge URL, and `oauth_route` uses it when creating the URL Composio should return to. It delegates the actual URL parsing to Python's standard `urlparse` helper.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Brokered tool execution
The broker-facing API, Composio client operations, and MCP Tool Router call path for discovering and running tools.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

Think of this file as the reception desk for all Composio-backed connectors. UFO asks for actions like “what tools does Gmail offer?”, “what arguments does this tool need?”, or “run this tool for this workspace.” The broker turns those requests into Composio API calls and turns Composio’s answers back into UFO’s standard connector objects.

A key detail is that the broker is intentionally stateless. It fetches the Composio client each time a method runs, instead of keeping one stored inside the class. That matters for tests and for safe connection behavior, because a changed transport or API setup is picked up immediately.

The file also protects users from confusing failures. If a tool slug is wrong, it tries to add a helpful list of real tool slugs. If an account grant is stale, meaning UFO no longer has the connected account Composio expects, it tells the agent to ask the member to reconnect instead of pretending the tool is missing.

Files get special treatment. Composio may return file objects hidden anywhere inside a tool response, so this broker searches nested data for downloadable file links. For uploads, it creates a temporary upload slot and rewrites file inputs into the vocabulary used by UFO’s workspace file flow. Credentials are also guarded: the broker checks that the connected account belongs to this workspace’s broker user, then returns a proxy transport rather than a raw provider token.

#### Function details

##### `ComposioBroker.tools`  (lines 48–49)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: This asks Composio for tools available for one provider, such as a specific app or service, and returns them in UFO’s standard tool format. It is used when the system needs to show or search the actions a connector can perform.

**Data flow**: It receives a workspace identifier, provider name, and search query. It asks the current Composio client to list matching tools, then passes those raw rows through a converter that keeps the slug, short description, and input shape. It returns a tuple of `BrokerTool` objects.

**Call relations**: When discovery is needed, this method is the public broker entry point. It gets a fresh Composio client, asks Composio for matching tools, and hands the raw results to `_discovered_tools` so the rest of UFO sees a consistent shape.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 51–64)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: This fetches the detailed input schema for one Composio tool. A schema is the description of what arguments a tool accepts, like a form that says which fields are expected.

**Data flow**: It receives a workspace identifier, provider name, and tool slug. It asks Composio for that tool’s schema, rewrites any file-upload fields into UFO’s workspace-file format, and returns a `BrokerTool` with the slug, description, and input schema. If Composio says the tool does not exist, it raises `UnknownBrokerTool` so callers can treat it as a bad tool name.

**Call relations**: This method is called when UFO already has a tool slug and needs to know how to call it correctly. It uses the Composio client for the remote lookup and `workspace_file_schema` to make file inputs fit UFO’s own upload flow.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 66–89)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: This runs a Composio tool for a workspace and a connected account. It is the path that turns a chosen connector action and its arguments into a real server-side operation in Composio.

**Data flow**: It receives the workspace, provider, tool slug, tool arguments, connected account id, and optional idempotency key. It builds Composio’s external user id from the workspace id, sends the execution request, and returns Composio’s response dictionary. If execution fails because the connected account is stale, it changes the error into reconnect guidance; if the slug is missing, it tries to enrich the error with valid tool names.

**Call relations**: This is the broker’s main action runner. It calls `_stale_account` to distinguish dead account grants from other errors, `_reconnect_error` to produce user-facing reconnect advice, and `_slug_miss` to make wrong-tool-name errors more useful.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 91–96)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: This finds files produced by a Composio tool response. It matters because a tool may return downloadable files inside nested response data rather than in one fixed top-level field.

**Data flow**: It receives the full response dictionary from a tool execution. It creates an empty list, walks through the response looking for objects shaped like Composio file records, and returns the discovered files as `BrokerFile` objects.

**Call relations**: After a tool has run, callers can use this method to extract downloadable outputs. It delegates the recursive search to `_collect_files`, which does the actual walking through dictionaries and lists.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 98–114)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: This prepares a place to upload a file that will later be passed into a Composio tool. It is like reserving a locker before putting a package in it: Composio gives back the upload address and the reference the tool should use.

**Data flow**: It receives the workspace, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a `StagedUpload` containing the URL where bytes should be uploaded, the content type to use, and the argument object that should be supplied to the tool.

**Call relations**: This is used before tool execution when a tool needs a file input. It calls the Composio client to create the upload slot and packages the result in UFO’s standard staged-upload object.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 116–119)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: This searches connector tools using Composio’s Tool Router search. It helps find likely tools from a natural query rather than requiring an exact slug.

**Data flow**: It receives a workspace identifier, provider name, and query text. It gets the current Composio client and passes all of that to the Composio search helper. It returns a `BrokerSearch` result.

**Call relations**: This method is the broker’s search entry point. Instead of doing the search itself, it hands off to `search_connector_tools`, which knows how to talk to Composio’s tool-routing search service.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 121–137)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a safe credential object for making provider HTTP requests through Composio, without exposing the provider’s real secret token. It first checks that the connected account belongs to this workspace’s broker user, which prevents one workspace from accidentally using another workspace’s grant.

**Data flow**: It receives the workspace, provider, and connected account id. It builds the broker user id, asks Composio to confirm that the account is connected for that user and provider, and then returns a `Credential` whose transport proxies requests through Composio. If the account is not found, it returns a reconnect-style error instead of a credential.

**Call relations**: This method is used when another part of UFO needs authenticated provider HTTP access. It verifies ownership with the Composio client, uses `_reconnect_error` for stale grants, then wraps `ComposioProxyTransport` in a `Credential` so future requests go through Composio’s proxy.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 139–160)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: This improves a “tool not found” error by trying to include real tool slugs for the provider. It gives the agent a better next guess instead of only saying the requested slug failed.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the bad slug into search words, asks Composio for nearby tools, and if needed falls back to listing tools without a query. If it finds tools, it returns a new Composio error whose message includes available slugs; otherwise it returns the original error.

**Call relations**: This helper is called by `ComposioBroker.execute` only after Composio reports a missing tool slug. It uses `_discovered_tools` to normalize the candidate tools before adding their slugs to the error message.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 163–172)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: This searches through nested response data for Composio file objects. It recognizes files by the presence of a download URL, file name, and MIME type.

**Data flow**: It receives any value and a list that is being filled. If the value looks like a Composio file record with a non-empty `s3url`, it appends a `BrokerFile`. If the value is a dictionary or list, it recursively checks each contained value. It does not return a new value; it changes the `found` list in place.

**Call relations**: This is the worker used by `ComposioBroker.file_outputs`. The public method sets up the list and returns the final tuple, while this helper does the nested walk.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 175–186)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: This decides whether a Composio execution error is really saying the connected account is gone or no longer valid. That distinction matters because a stale account should lead to reconnect guidance, not a list of alternate tool names.

**Data flow**: It receives a Composio error and the account id that was used. It lowercases the error body and checks for narrow signs of a missing connected account: Composio’s own phrase “connected account” with “not found,” or the exact account id with “not found.” It returns `true` if the error looks like a stale grant and `false` otherwise.

**Call relations**: This helper is used by `ComposioBroker.execute` before treating an error as a missing slug. It keeps provider-domain errors from being misread as stale grants by matching only very specific wording.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 189–190)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: This turns a Composio account-related failure into an error message that tells the user to reconnect the provider. It keeps the original failure but adds guidance that is useful to the agent or member.

**Data flow**: It receives the original Composio error and provider name. It asks the connector layer for standard stale-grant guidance for that provider, appends that guidance to the original error body, and returns a new `ComposioError` with the same status code.

**Call relations**: Both `ComposioBroker.execute` and `ComposioBroker.credential` call this when they detect that the connected account cannot be used. It centralizes the wording so stale grants are explained consistently.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 193–213)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: This converts raw tool rows from Composio into UFO’s `BrokerTool` objects. It filters out unusable rows and trims long descriptions so discovery results stay concise.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it picks a slug from `slug` or `name`, skips rows without a valid slug, shortens the description if needed, rewrites file-upload schema fields into workspace-file schema fields, and builds a `BrokerTool`. It returns all converted tools as a tuple.

**Call relations**: This helper is used by `ComposioBroker.tools` for normal discovery and by `ComposioBroker._slug_miss` when building a helpful missing-tool error. It is the shared adapter from Composio’s catalog format to UFO’s connector format.

*Call graph*: called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector OAuth, tool discovery, and tool execution request handling`

Composio acts like a switchboard for many third-party services. Instead of this project storing a separate password or token for every service, Composio stores those secrets and this client asks Composio to connect, discover, and run tools on behalf of a workspace user. Without this file, the connector system could not create OAuth consent links, verify that a finished connection belongs to the right workspace, discover which Composio tools are safe to offer, or execute those tools.

The file has three main jobs. First, it defines small records for connector details, upload slots, and tool-router sessions. Second, it filters Composio’s large public catalog so the project only offers toolkits that have managed authentication, have actual tools, and are not on a hand-written banned list of services that are known to be incomplete or unsafe for this use. Third, `ComposioClient` wraps Composio’s REST API using `httpx`, an HTTP library for making web requests.

A typical flow is: make a client using the deploy’s `COMPOSIO_API_KEY`, ask Composio for a connection link, later confirm the connected account is active and belongs to the expected workspace user, then search for useful tools and execute one. File inputs get special treatment: schemas that mention Composio’s raw file-upload format are rewritten so the agent only sees a simple workspace file path. Tool search also uses a cached Tool Router session, like keeping one help desk window open for repeated questions about the same connector.

#### Function details

##### `connectable`  (lines 122–146)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit is safe and useful enough for this project to offer to users. It rejects toolkits that are banned by project policy, have no Composio-managed sign-in option, or list no tools.

**Data flow**: It receives a toolkit slug, such as a service name, and a catalog record from Composio. It checks the slug against the banned list, then reads the catalog record for managed authentication schemes and a tool count. It returns `true` only when all checks pass; otherwise it returns `false`.

**Call relations**: When the client checks one specific toolkit or searches many toolkits, those flows call this function before showing anything to a user. It is the local gatekeeper between Composio’s broad catalog and the smaller set this system is willing to broker.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 153–156)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception for Composio failures. It keeps both the HTTP status code and the response body so callers can tell what went wrong instead of silently treating a bad connection as empty data.

**Data flow**: It receives a numeric status and a text body from a failed or unusable Composio response. It builds a readable error message and stores the original status and body on the error object. The result is an exception ready to be raised.

**Call relations**: Many client methods raise this when Composio replies with an error or with data that is missing required fields. `_body` uses it for general HTTP and response-shape failures, while higher-level methods use it for business checks such as wrong account owner or missing upload URL.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 173–182)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to authorize a connector through Composio. This is the start of the consent flow, similar to sending someone to a secure checkout page rather than collecting their card number yourself.

**Data flow**: It receives a toolkit slug, a broker user id, and a callback URL. It first finds or creates an auth configuration for that toolkit, then posts those details to Composio’s connected-account link endpoint. It returns the redirect URL that the user should visit, or raises an error if Composio does not provide one.

**Call relations**: This method starts by calling `_auth_config` because Composio needs to know which authentication setup to use. It then uses `_post` to ask Composio for the link, and raises `ComposioError` if the answer cannot be used.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 184–208)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Verifies that a completed Composio connection is the right one before the project trusts it. It checks that the account belongs to the expected workspace user, is active, and is for the expected toolkit.

**Data flow**: It receives a connected account id, the expected user id, and the expected toolkit slug. It fetches the account from Composio, checks ownership, status, and toolkit identity, then returns an `OAuthAccount` containing the connected account id. If any check fails, it raises an error instead of producing a grant.

**Call relations**: This is used after the user comes back from the consent flow. It calls `_get` to read the account metadata and uses `ComposioError` to stop cross-user, inactive, or wrong-toolkit accounts from being accepted.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.account_label`  (lines 210–213)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a friendly name for a connected account, if Composio has one. This can help show users which account they connected.

**Data flow**: It receives a connected account id and fetches that account record from Composio. It reads the `alias` field and returns it only if it is a non-empty string. If there is no usable alias, it returns `null`.

**Call relations**: This is a small read-only helper around `_get`. It fits into user-facing display flows where the system wants a label, not the full account record.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 215–245)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists tools available inside one Composio toolkit, optionally narrowed by a query. It follows Composio’s pages so tools beyond the first page are not missed.

**Data flow**: It receives a toolkit slug and an optional search query. It repeatedly asks Composio for pages of tool records, collects dictionary-shaped items, follows `next_cursor` when there is another page, and stops at the final page or at the local maximum. It returns a tuple of tool records.

**Call relations**: The broker uses this when it needs to recover from or explain an unknown tool slug. Internally it relies on `_get` for each page and keeps the paging details hidden from callers.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 247–248)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full schema for one Composio tool. A schema describes what inputs the tool expects, like a form definition for the agent to fill in.

**Data flow**: It receives a tool slug, requests that tool’s record from Composio, and returns the response dictionary. It does not transform the schema itself.

**Call relations**: This is a direct wrapper around `_get`. Other connector code can call it when it needs the exact details for a specific tool rather than a list of tools.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 250–267)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a single user-supplied toolkit slug is valid and connectable, and returns the display name if it is. It also blocks suspicious slugs that are not plain toolkit identifiers, so user input cannot be turned into an unexpected API path.

**Data flow**: It receives a slug string. It first checks that the slug contains only allowed characters, then fetches the toolkit from Composio. A missing toolkit returns `null`; other API errors are raised. If the toolkit passes `connectable`, it returns the toolkit name or the slug; otherwise it returns `null`.

**Call relations**: Connector resolution code uses this to decide whether it can claim an open-catalog connector. It calls `_get` for the catalog lookup and `connectable` for the local policy decision.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 269–287)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio’s toolkit catalog and returns only the services this project can actually offer. This powers discovery without exposing users to broken or disallowed connectors.

**Data flow**: It receives a search query and a limit. It asks Composio for matching toolkits, loops over the returned items, keeps only valid dictionary records with a slug that passes `connectable`, and returns pairs of slug and display label.

**Call relations**: This is the catalog-search companion to `connectable_toolkit`. It calls `_get` to fetch Composio’s search results and `connectable` to apply project rules to each candidate.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 289–303)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool through Composio’s server-side execution API. The user’s service token stays inside Composio; this system sends only the tool name, user identity, account id if needed, and arguments.

**Data flow**: It receives a tool slug, argument values, a broker user id, and optional connected account and idempotency key. It builds the request body, rejects it if the JSON payload is over the size limit, adds an idempotency header when provided, and posts the execution request. It returns Composio’s response dictionary.

**Call relations**: Higher-level connector execution code calls this when the agent has chosen a tool to run. It uses `_post` for the actual HTTP request, while the size check protects Composio and this service from unexpectedly huge tool inputs.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 305–329)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file before a tool uses it. The returned upload slot includes the file-store key the tool should reference and, when needed, a temporary URL where the sandbox can upload the bytes.

**Data flow**: It receives toolkit and tool slugs plus filename, MIME type, and MD5 hash. It posts an upload request to Composio, checks that a store key is present, then reads the optional presigned upload URL. It returns a `ComposioUpload`, or raises an error if the response is missing required data.

**Call relations**: File-capable tool execution uses this before running a tool that needs an uploaded file. It calls `_post` to mint the upload slot and raises `ComposioError` when Composio’s answer cannot safely be used.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 331–342)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The Tool Router is a Composio service that can search tools by use case rather than exact tool name.

**Data flow**: It receives a broker user id and a list of toolkit slugs. It posts a session request to Composio, then extracts the session id and MCP URL. MCP means Model Context Protocol, a standard way for tools to be exposed to an AI client. It returns a `ToolRouterSession` or raises an error if the response lacks the needed fields.

**Call relations**: `search_connector_tools` calls this when no cached search session exists for a user and connector. It uses `_post` for the session request and hands back the endpoint that later receives the search call.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 344–362)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration Composio should use for a toolkit, creating a Composio-managed one if none exists. This lets operator-created custom configurations take priority while still making ordinary managed OAuth work automatically.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts an id if one exists. If not, it posts a request to create a managed auth config, then extracts and returns the new id. If Composio does not return an id, it raises an error.

**Call relations**: `connect_link` calls this before creating a consent link. It uses `_get`, `_auth_config_id`, and `_post` to hide the details of whether the config already existed or had to be created.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 364–366)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a GET request to Composio and returns a checked response body. GET requests are used for reading data, such as account records, tool schemas, and catalog entries.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the GET request, passes the response to `_body`, and returns the parsed dictionary. The temporary HTTP client is closed when the request finishes.

**Call relations**: Most read-only client methods call this instead of using `httpx` directly. It calls `_http` to build the configured client and `_body` to turn the HTTP response into either useful data or a clear error.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_auth_config, account_label, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 368–372)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a POST request to Composio and returns a checked response body. POST requests are used for actions such as creating links, executing tools, creating uploads, and opening router sessions.

**Data flow**: It receives an API path, a JSON body, and optional headers. It opens an HTTP client, sends the POST request, validates and parses the response through `_body`, and returns the resulting dictionary. The HTTP client is closed afterwards.

**Call relations**: Action-oriented methods call this to avoid repeating HTTP setup and response checking. Like `_get`, it depends on `_http` for the configured transport and `_body` for shared error handling.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 374–380)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds an asynchronous HTTP client already configured for Composio. It sets the base URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the client’s API key and optional transport. It creates and returns an `httpx.AsyncClient`, which is an async web-request client that can be used with `await`. Nothing is sent until `_get` or `_post` uses it.

**Call relations**: `_get` and `_post` call this for every request. Keeping this setup in one place ensures all Composio calls use the same base address, authentication header, timeout, and test override behavior.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 383–391)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a usable dictionary, or raises a clear error. It is the shared checkpoint that prevents bad status codes or surprising response shapes from leaking upward.

**Data flow**: It receives an `httpx.Response`. If the status code is an error, it raises `ComposioError` with the response text. If there is no content, it returns an empty dictionary. Otherwise it parses JSON and requires it to be an object; non-object JSON also becomes a `ComposioError`.

**Call relations**: Both `_get` and `_post` send all responses here. Higher-level methods can then assume they either received a dictionary or an exception, instead of checking HTTP details themselves.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 394–420)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the project’s simpler workspace-file format. This keeps the agent from seeing internal storage fields like file keys and instead asks for a normal `/workspace` path.

**Data flow**: It receives any schema-like value. If it finds a dictionary marked as file-uploadable, it replaces it with an object schema containing a `workspace_file` string and preserves the description when present. For other dictionaries and lists, it walks through their contents recursively. Non-container values are returned unchanged.

**Call relations**: `_search_result` calls this while turning Tool Router search results into broker tool descriptions. It sits between Composio’s raw schema language and the simpler input language the dynamic connector tools expose.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 423–430)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first usable auth configuration id from a Composio list response. It is a small helper for the common shape returned by the auth-config search endpoint.

**Data flow**: It receives a response dictionary. It looks for an `items` list, then scans for the first dictionary item whose `id` is a string. It returns that id, or `null` if no suitable id is found.

**Call relations**: `_auth_config` calls this after asking Composio whether an auth config already exists. If this helper returns nothing, `_auth_config` proceeds to create a new managed configuration.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 433–440)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the standard Composio client for this deployment using the `COMPOSIO_API_KEY` environment variable. It fails early if the key is missing because connector OAuth cannot work without broker credentials.

**Data flow**: It reads the environment for `COMPOSIO_API_KEY`. If the value is absent or empty, it raises a runtime error. Otherwise it returns a new `ComposioClient` carrying that API key.

**Call relations**: Application code uses this factory when it needs the real deployment client. Tests can still construct `ComposioClient` directly with a mock transport, but production paths get their key from the environment here.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 447–448)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This avoids crashes when Composio or the Tool Router returns missing or differently shaped fields.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: `_search_result` uses this repeatedly while reading nested search-result data. It lets that parser be forgiving about optional sections without scattering type checks everywhere.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 451–454)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It filters out missing, empty, or non-string values.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only entries that are non-empty strings and returns them as a tuple.

**Call relations**: `_search_result` uses this for tool slugs, plan steps, guidance, and pitfalls coming back from the Tool Router. It keeps the final broker search result clean even if some fields are absent or mixed.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 457–491)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts a Composio Tool Router search response into the project’s `BrokerSearch` format. The result includes tool choices, input schemas, recommended plan steps, guidance, and warnings about pitfalls.

**Data flow**: It receives a search-result dictionary. It finds the nested data and tool schemas, walks each result item, collects primary and related tool slugs without duplicates, builds `BrokerTool` records with cleaned file schemas, and gathers plan, guidance, and pitfall text. It returns a `BrokerSearch` object containing those pieces.

**Call relations**: `search_connector_tools` calls this after the MCP search call returns. Inside, it relies on `_dict`, `_str_tuple`, and `workspace_file_schema` to turn a flexible external response into the stable format used by the broker.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 494–517)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for the best Composio tools for a user’s natural-language request within one connector. It uses Composio’s Tool Router for semantic search, meaning the query can describe a goal rather than name an exact tool.

**Data flow**: It receives a Composio client, workspace id, connector slug, and query text. It builds the broker user id, reuses a cached Tool Router session for that user and connector when available, or creates one under a lock so concurrent searches do not open duplicates. It calls the router’s `COMPOSIO_SEARCH_TOOLS` tool through MCP, then converts the response with `_search_result` and returns a `BrokerSearch`.

**Call relations**: This is the high-level search entry point in this file. It calls `tool_router_session` only when a session is missing, hands the query to `mcp_session.mcp_call_tool`, and then delegates response shaping to `_search_result`.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio's Tool Router. The Tool Router offers a search tool named `COMPOSIO_SEARCH_TOOLS` through MCP, which means Model Context Protocol: a standard way for software to expose tools to AI systems. Here, MCP is reached over streamable HTTP, meaning a web connection that can carry streamed protocol messages.

The main job is simple: open a temporary connection to a Composio MCP endpoint, call one named tool with given arguments, close the connection, and return the answer as an ordinary dictionary. This matters because the rest of the code wants a clean result, not several possible MCP response shapes.

The file is careful about response formats. If the MCP library has already parsed a dictionary into `data`, it returns that. If not, it checks `structured_content`. If the answer only arrived as a text block, it tries to read that text as JSON. If the text is not JSON, it returns it under a `text` key. As a last fallback, it wraps whatever raw data exists under `result`.

A useful analogy is a translator at a service desk: no matter whether the answer comes as a form, a structured record, or a note, this file hands the rest of the system one predictable envelope.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one tool on a remote MCP endpoint and returns the result as a plain dictionary. It is used when the project needs to ask Composio's Tool Router for information, especially tool search results, without exposing the rest of the code to MCP connection details.

**Data flow**: It receives an endpoint URL, a tool name, tool arguments, HTTP headers, and a timeout. It opens a streamable-HTTP MCP client, sends the tool call, waits for the response, then closes the client session. After that it looks for the most useful response shape: first parsed dictionary data, then structured dictionary content, then JSON inside a text response, and finally a fallback dictionary containing the raw result.

**Call relations**: This function creates the `StreamableHttpTransport` needed to talk to the remote MCP server, passes it into `fastmcp.Client`, and uses that client to call the requested tool. If the reply arrives as text, it hands the text to `json.loads` so a JSON-looking answer can become a normal dictionary for the caller.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### Credential-safe request proxying
HTTP request rewriting and response normalization for calls routed through Composio without exposing provider credentials.

### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Some connectors need to fetch data from services such as Google Sheets or another provider, but Composio deliberately keeps the real provider credential hidden. This file is the bridge that makes that possible. It acts like a mail forwarding desk: the connector writes a normal request to the provider, this code repackages it for Composio's proxy endpoint, Composio adds the secret credential server-side, and the response is unpacked so the connector can keep working as if it talked to the provider directly.

The main piece, ComposioProxyTransport, plugs into httpx, the HTTP client library. When a request comes in, it copies the method, full URL, useful headers, and body into a JSON payload for Composio's /tools/execute/proxy endpoint. It skips headers that would be wrong or unsafe to forward, such as authorization and content-length. It also carries timeout information so a stuck broker call cannot wait forever.

When Composio replies, the transport reads the response, optionally with a size limit, and rebuilds the provider response. JSON bodies are returned normally. Large or non-JSON binary bodies are special: Composio stores them elsewhere and returns a temporary download URL, so this code turns that into a 302 redirect with a clear text explanation. ComposioRequestForwarder uses the same transport for one-off broker forwarding, with response-size and wall-clock timeout protection.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 66–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request-rewriting step. It takes a normal provider HTTP request, wraps it as a Composio proxy-execute request, sends it through the inner HTTP transport, and returns a response that looks like it came from the original provider.

**Data flow**: It receives an httpx request containing a method, URL, headers, body, and optional timeout. It reads the body, builds a JSON payload with the connected account id and request details, drops unsafe or transport-specific headers, and sends that payload to Composio's proxy endpoint with the Composio API key. It then reads Composio's reply with _read_bounded; if Composio itself reports an error, it returns that error body, otherwise it passes the decoded JSON into _provider_response and returns the reconstructed provider response.

**Call relations**: This function is called when httpx uses ComposioProxyTransport as its transport, and it is also used directly by ComposioRequestForwarder.forward. During the flow it delegates response-size protection to _read_bounded, then delegates the final unpacking of Composio's response format to _provider_response.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response from Composio while optionally enforcing a maximum size. The size cap prevents a shared proxy process from filling memory with a very large provider response.

**Data flow**: It receives an httpx response from the Composio broker. If no limit is configured, it simply reads all bytes and returns them. If a limit is configured, it reads the response chunk by chunk, adds each chunk to a growing byte buffer, and checks the total size; if the response grows past the limit, it closes the response and raises a ComposioError instead of buffering more.

**Call relations**: ComposioProxyTransport.handle_async_request calls this right after receiving the broker response. It gives the rest of the proxy code either a complete byte string to parse or a clear failure when the broker response is too large.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio's proxy-execute result back into a normal httpx response for the connector. It hides Composio's wrapper format so downstream code can keep reading status codes, headers, and bodies in the usual way.

**Data flow**: It receives a decoded JSON payload from Composio plus the original request. It unwraps nested data envelopes, extracts the provider status and headers, and removes body-related headers that could be wrong after reconstruction. If Composio reports binary_data, it validates the download URL and returns a 302 redirect pointing to it. Otherwise it converts JSON-like data, text data, empty data, or other simple values into response bytes and returns an httpx response with the reconstructed status, headers, content, and original request attached.

**Call relations**: ComposioProxyTransport.handle_async_request calls this after the broker response has been read and decoded. This function is the final translator between Composio's proxy response shape and the ordinary HTTP response shape expected by provider connectors.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport. It is used to release network resources when the proxy transport is no longer needed.

**Data flow**: It receives no new data beyond the transport object itself. It calls close on the inner transport, which gives the underlying HTTP layer a chance to shut down sockets and cleanup state. It does not return a value.

**Call relations**: ComposioRequestForwarder.forward calls this in a finally block after forwarding a request, so cleanup happens even if the request fails or times out. It simply passes the close request down to the inner transport.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a command-line or broker-style credential flow. It wraps the whole exchange in a firm time limit and response-size limit so a slow or huge provider response cannot wedge the proxy process.

**Data flow**: It receives an account id, HTTP method, target URL, headers, and body bytes. It gets the current Composio client and API key, builds a ComposioProxyTransport for that connected account, creates an httpx request with the incoming data and timeout settings, and asks the transport to execute it. It reads the returned response body, converts the status, headers, and bytes into a ForwardedResponse, and always closes the transport afterward. If the wall-clock timeout expires, it raises a ComposioError with a gateway-timeout style status.

**Call relations**: This function is the one-off forwarding entry in this file. Instead of an httpx client naturally calling the transport, it constructs ComposioProxyTransport itself and directly calls handle_async_request. It then packages the result for the broader connector or broker layer as a ForwardedResponse.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).

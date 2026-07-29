# Connector, MCP, research, coding, REPL, todo, and external-app tools  `stage-10.3`

This stage is the toolbox layer used during the agent’s main work, with some shared support behind the scenes. It lets the agent reach outside services, run code, search, and keep track of work without exposing private credentials.

The connector tools are the front desk: they list connected services, show what each can do, run chosen tools, and pass files safely. Behind that, Pipedream and Composio brokers translate UFO’s tool calls into calls to those outside platforms. Their clients create login links, check connected accounts, run actions, and handle errors. Their proxy helpers let UFO call Gmail, GitHub, or similar services through the connector, so secret tokens stay with the connector provider. Composio also has a resolver for finding toolkits by name and an MCP session for one remote tool search.

MCP support lets workspace-configured tool servers advertise and run tools. Research tools use Exa or another search provider to search the web and fetch pages. The REPL extension runs repeated Python or JavaScript snippets. Slack and YC tools guide setup and queries. Todos store task checklists. The evaluation environment provides test email, calendar, and code-search tools through the same connector path.

## Files in this stage

### Pipedream connector bridge
Pipedream integration files discover actions, validate connected accounts, run provider tools, and proxy authenticated provider requests.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Think of this file as a concierge desk for Pipedream actions. The rest of the system asks for a tool like “send an email” or “create a calendar event,” and this broker translates that into Pipedream’s language. It looks up which actions exist for a provider, turns Pipedream’s action definitions into simple tool descriptions, and hides account-binding details so the agent does not have to pass secret credentials directly.

When an action is run, the broker fetches the action definition, finds the special “app account” input slot, inserts the connected account into that slot, checks that the account really belongs to the requested workspace and app, and then asks Pipedream to run the action on its server. If the action name is wrong, it tries to return a helpful error listing real available action keys. If the connected account is stale or missing, it adds guidance telling the user to reconnect.

The file also handles two file-related rules. Output files are read from Pipedream’s File Stash export list and returned as downloadable URLs. Input files are not uploaded through this broker, because Pipedream actions expect file URLs instead; callers must share a workspace file and pass that URL.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–67)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds available Pipedream actions for a given connector provider and search text. It avoids leaving the caller with no options when Pipedream’s strict search returns nothing by falling back to the app’s top actions.

**Data flow**: It receives a workspace id, a provider name, and a search query. It looks up the provider’s Pipedream app slug, asks the Pipedream client for matching actions, converts the raw response into BrokerTool objects, and if a non-empty query found nothing, asks again with an empty query. It returns a tuple of tools the agent can choose from.

**Call relations**: This is used directly by search when the system wants catalog results. It relies on _spec to translate the provider into a Pipedream app, pipedream_client to talk to Pipedream, and _listed_tools to turn Pipedream’s response into the project’s common tool shape.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 69–75)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one Pipedream action. The result tells the agent which arguments it may supply, while hiding internal Pipedream fields and the connected-account slot.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts the configurable properties, converts those properties into a JSON schema, and returns a BrokerTool with the slug, description, and input schema. It does not include account-binding fields because the broker fills those in later.

**Call relations**: This is called when the system needs the detailed shape of one tool before using it. It calls _definition to fetch the action details, _props to pull out configurable fields, _input_schema to make a standard schema, and _str to safely read text descriptions.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 77–112)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It protects the caller from credential details by binding the account into the action definition itself.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, connected account id, and an optional idempotency key. It fetches the action definition, copies the arguments, adds the account into the action’s app slot, verifies that the account belongs to the workspace and matches the requested app, and then asks Pipedream to run the action. It returns the response dictionary, or raises a clear error if the action is unknown, the account is wrong or stale, or the action reports a failure.

**Call relations**: This is the main run path for Pipedream tools. It calls _definition first; if the action key is missing, it asks _key_miss to make a more helpful error. It uses _app_slot to find where the account must be inserted, _spec to check the app, and _stale_account plus _reconnect_error to turn stale-account failures into reconnect guidance.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 114–132)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files created by a completed Pipedream action and presents them as named download links. This lets the sandbox fetch files that the action wrote during its run.

**Data flow**: It receives the action response dictionary. It looks inside the response exports for Pipedream’s File Stash upload list, skips malformed entries, takes each valid download URL, derives a simple filename from the local path if one is present, and returns BrokerFile objects. It does not download the files itself.

**Call relations**: This is used after execute returns, when the system wants to expose files produced by the remote action. It creates BrokerFile records from Pipedream’s File Stash metadata and uses PurePosixPath only to turn a container path into a filename.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 134–146)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged uploads for Pipedream actions. Pipedream expects file inputs as URLs, not as files pre-uploaded through this broker.

**Data flow**: It receives upload details such as filename, MIME type, and checksum, but does not use them to create an upload. Instead, it raises an error explaining that callers should share the workspace file and pass the resulting download URL.

**Call relations**: This function exists to satisfy the broker interface while making Pipedream’s file-input rule explicit. It does not call other helpers because the correct behavior is simply to stop and give the caller clear instructions.


##### `PipedreamBroker.search`  (lines 148–149)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns search results for Pipedream actions in the common BrokerSearch format. Pipedream has no separate planning or routing layer here, so the result is just the tools list.

**Data flow**: It receives a workspace id, provider, and query. It calls tools to find matching actions and wraps those tools in a BrokerSearch object. The output is a search result object the rest of the connector system understands.

**Call relations**: This is a small wrapper around PipedreamBroker.tools. When higher-level code asks to search a connector, this method delegates the actual catalog lookup to tools and then packages the answer.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 151–172)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can send requests through Pipedream’s Connect Proxy for one connected account. It first verifies that the account belongs to the workspace and the expected app.

**Data flow**: It receives a workspace id, provider, and account id. It looks up the provider spec, fetches the connected account from Pipedream, turns a missing account into reconnect guidance, checks that the account’s app matches the provider, and then returns a Credential containing a PipedreamProxyTransport. That transport knows how to proxy requests using the connected account.

**Call relations**: This is used when the system needs a transport for making app API calls through Pipedream rather than running a Pipedream action. It uses _spec for provider lookup, _reconnect_error for missing-account guidance, pipedream_client for Pipedream access, and PipedreamProxyTransport to build the actual request path.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 174–182)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for one action slug. If Pipedream says the action does not exist, it turns that into the project’s UnknownBrokerTool signal.

**Data flow**: It receives an action slug. It asks the current Pipedream client for the action definition, treats a 404 response as an unknown tool, and otherwise returns the definition data as a dictionary. If the response has a nested data object, it returns that; otherwise it returns the whole payload.

**Call relations**: This helper is shared by schema and execute. Schema uses it to describe an action’s inputs, while execute uses it to know how to bind the connected account before running the action.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 184–198)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when an action slug is unknown during execution. Instead of only saying “not found,” it tries to include real action keys for that app so the next attempt can be corrected.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider’s app, tries to list that app’s available actions, converts them into tools, joins their slugs into a readable list, and returns a PipedreamError with that guidance. If the catalog lookup itself fails, it returns a simpler not-found error.

**Call relations**: Execute calls this after _definition reports an unknown action. This helper uses _spec to identify the app, list_actions to ask Pipedream for available actions, and _listed_tools to extract clean action slugs.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 201–208)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Detects whether a Pipedream error probably means the connected account is stale or no longer known. This lets the system tell the user to reconnect instead of showing a confusing low-level failure.

**Data flow**: It receives a PipedreamError and the account id that was used. It lowercases the error body and checks for narrow phrases such as “external user not found” or the exact account id combined with “not found.” It returns true only when the wording strongly points to a stale grant.

**Call relations**: Execute uses this after Pipedream run failures and action-level errors. If it returns true, execute passes the error to _reconnect_error so the user gets reconnect instructions.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 211–212)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds human guidance to an error when the connected account likely needs to be reconnected. It keeps the original status and message, then appends provider-specific reconnect advice.

**Data flow**: It receives a PipedreamError and provider name. It asks the connector system for stale-grant guidance for that provider, appends that text to the original error body, and returns a new PipedreamError with the same status code.

**Call relations**: Execute calls this when a run failure appears to involve a stale account. Credential also calls it when the requested account is missing from the workspace.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 215–219)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the Pipedream connector specification for a provider name. This is how the broker translates a project-level provider into Pipedream’s app identity.

**Data flow**: It receives a provider string. It checks the Pipedream connector registry for that provider and returns the matching ConnectorSpec. If the provider is not registered, it raises a KeyError so the mistake is caught clearly.

**Call relations**: Tools, execute, credential, and _key_miss all depend on this lookup before talking to Pipedream. It is the common gate that makes sure the provider name is known to the Pipedream extension.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 222–234)

```
def _listed_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream’s raw action-list response into the project’s simpler BrokerTool records. It keeps only usable action keys and short descriptions.

**Data flow**: It receives a dictionary returned by Pipedream’s list-actions API. It reads the data list, skips non-dictionary or keyless entries, turns each valid action key into a BrokerTool, and returns a tuple of those tools. Missing or non-string descriptions become empty strings.

**Call relations**: Tools uses this to return catalog search results. _key_miss also uses it to build the list of available action keys for a helpful not-found error.

*Call graph*: calls 1 internal fn (_str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 237–239)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable properties from a Pipedream action definition. These properties describe the inputs that an action can accept.

**Data flow**: It receives an action definition dictionary. It reads the configurable_props field, keeps only entries that are dictionaries, and returns them as a list. If the field is missing or not a list, it returns an empty list.

**Call relations**: Schema calls this before building the public input schema. _app_slot calls it when looking for the special account-binding property.

*Call graph*: called by 2 (schema, _app_slot).


##### `_app_slot`  (lines 242–249)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special input field where the broker must place the connected account. Without this slot, the action cannot run with the user’s granted account.

**Data flow**: It receives an action definition and slug. It scans the definition’s configurable properties for the property whose type marks it as the Pipedream app/account field, and returns that property’s name. If no such field exists, it raises a PipedreamError explaining that the action cannot bind an account.

**Call relations**: Execute calls this just before running an action. It uses _props to inspect the definition, then gives execute the field name where it should insert the account id information.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 252–275)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds a standard JSON schema for the action inputs the agent is allowed to set. A JSON schema is a machine-readable description of fields, types, and required values.

**Data flow**: It receives a list of Pipedream property dictionaries. It skips unnamed fields, internal Pipedream fields, the app/account slot, and service-only properties. For the remaining fields, it maps Pipedream types to JSON types, copies a useful description or label when present, marks non-optional fields as required, and returns a schema dictionary.

**Call relations**: Schema calls this after _props extracts the raw configurable properties. It uses _str to safely read each property type before deciding whether and how to include it.

*Call graph*: calls 1 internal fn (_str); called by 1 (schema).


##### `_str`  (lines 278–279)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. This prevents accidental non-text values from leaking into descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not modify anything else.

**Call relations**: Schema uses this for action descriptions, _listed_tools uses it for listed-action descriptions, and _input_schema uses it when reading property types.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector auth and connector action requests`

This file is the bridge between this project and Pipedream Connect. Pipedream acts like a trusted doorman for outside services: the user signs in through Pipedream, Pipedream keeps the real provider token, and this app only stores a connected-account ID. That matters because the app can use services such as Gmail without handling sensitive OAuth tokens itself.

The file defines which Pipedream-backed connectors are allowed here, currently Gmail. It also defines small data shapes for a consent token and a connected account. The main class, PipedreamClient, speaks to Pipedream’s REST API using httpx, an async HTTP library. It first gets a project access token using client credentials from environment variables, caches that token until it is close to expiring, and then uses it for later calls.

A major safety theme is ownership checking. A Pipedream project token can read accounts across the project, so this file checks that a connected account belongs to the expected external user or workspace before using it. Without those checks, one user might accidentally or maliciously cause the system to act through another user’s connection.

The client also searches action catalogs, fetches action definitions, and runs actions server-side. When it runs an action, it asks Pipedream to use a fresh file stash so files produced inside Pipedream can come back as downloadable URLs instead of unreachable temporary paths.

#### Function details

##### `PipedreamError.__init__`  (lines 79–82)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error object when Pipedream fails or returns data this client cannot safely use. It keeps both the HTTP-style status code and the response body so callers can decide how to report or react to the problem.

**Data flow**: It receives a status number and a text body, builds a message like “pipedream 403: ...”, stores the status and body on the error, and returns an exception object ready to be raised.

**Call relations**: This is the common failure language for this file and nearby broker code. Client methods use it when token grants, account records, ownership checks, or response shapes are wrong; broker code also raises it when connector setup or execution cannot proceed safely.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 120–141)

```
async def access_token(self) -> str
```

**Purpose**: Gets the access token this server needs in order to call Pipedream’s API. It reuses a cached token when it is still fresh, avoiding an extra login request on every call.

**Data flow**: It reads the client ID from the instance and checks the process-wide token cache. If a usable token is already there, it returns it. Otherwise it opens an HTTP client, posts the client ID and secret to Pipedream’s OAuth token endpoint, checks the response body, stores the new token with its expiry time, and returns the token string.

**Call relations**: The lower-level request helpers _get and _post call this before making authenticated Pipedream calls. It uses _http to create the temporary HTTP client and _body to turn the HTTP response into a checked dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 143–158)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted consent link for a user. This is what lets the user open a browser page and approve access to a provider account.

**Data flow**: It receives an external user ID plus success and error return URLs. It sends those to Pipedream, expects back a token and connect-link URL, checks both are non-empty strings, and returns them packaged as a ConnectToken.

**Call relations**: This is used during the start of an account-connection flow. It hands the actual API call to _post, and raises PipedreamError if Pipedream does not return the link needed to continue.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 160–167)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Looks up one connected account and verifies it belongs to the expected external user. This prevents the system from using a Pipedream account ID that belongs to someone else.

**Data flow**: It receives an account ID and expected external user ID. It fetches the account from Pipedream, unwraps the returned record if it is nested under data, then asks _owned_account to check ownership and health. It returns a ConnectedAccount if the record is safe to use.

**Call relations**: This is part of the safety gate after an account ID is presented. It uses _get for the network read, _dict for safe record extraction, and _owned_account for the ownership check.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.workspace_account`  (lines 169–179)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Looks up a connected account and verifies it belongs to the given workspace. This is useful when execution should be allowed for any valid connection user under that workspace, not just one exact external user string.

**Data flow**: It receives an account ID and workspace UUID. It fetches the account record, converts it into a ConnectedAccount, checks whether the account’s external user ID matches the workspace’s allowed naming pattern, and returns the account if allowed. If not, it raises a 403 PipedreamError.

**Call relations**: This function is an ownership guard before workspace-scoped execution. It uses _get to read from Pipedream, _account to validate the account record, and _workspace_owns_external_user to decide whether the external user belongs to the workspace.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 181–195)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the newest connected account for a specific external user and Pipedream app. This is used after a consent flow completes, when the system needs to identify the account the user just connected.

**Data flow**: It receives an external user ID and app slug. It asks Pipedream for matching accounts, keeps only dictionary-like records, chooses the one with the newest created_at value, checks that it has an ID, verifies it belongs to the external user, and returns a ConnectedAccount.

**Call relations**: This usually follows a browser consent return. It uses _get for the account list, _dict to ignore malformed records, and _owned_account to enforce that the returned account really belongs to the state-scoped user.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 197–203)

```
async def list_actions(self, app: str, query: str='', limit: int=ACTION_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Searches Pipedream’s catalog of available actions for one app, such as Gmail. This lets the system discover what tool-like operations Pipedream can run.

**Data flow**: It receives an app slug, an optional search phrase, and a result limit. It builds query parameters, sends them to Pipedream, and returns the response dictionary from the actions catalog.

**Call relations**: The Pipedream broker calls this when it needs to find actions matching a missing or searched-for tool key. The actual HTTP work is delegated to _get.

*Call graph*: calls 1 internal fn (_get); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 205–206)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition for one Pipedream action component. This tells the rest of the system what inputs the action expects and how it is described.

**Data flow**: It receives a component key, requests that component from Pipedream, and returns Pipedream’s response dictionary.

**Call relations**: This is a catalog-detail call that sits above _get. Higher-level code can use it after finding an action key to understand how to configure or present that action.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 208–226)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on the server side for a given external user and set of configured inputs. It also asks Pipedream to collect output files into a file stash so they can be accessed later.

**Data flow**: It receives an action key, external user ID, and configured properties. It builds a run request containing those values and a fresh stash marker, checks that the serialized request is not larger than the allowed limit, sends it to Pipedream, and returns the response dictionary.

**Call relations**: This is the execution endpoint for dynamic connector tools. It relies on _post for the authenticated HTTP call, and it is designed so the account credential stays inside Pipedream rather than being exposed to this app.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 228–231)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked response body. It is a shared helper so all read-style API calls get the same token and error handling.

**Data flow**: It receives an API path and optional query parameters. It gets an access token, opens an HTTP client with that token, sends the GET request, passes the response through _body, and returns the resulting dictionary.

**Call relations**: Public methods such as connected_account, workspace_account, newest_account, list_actions, and action_definition call this whenever they need to read data from Pipedream. It in turn depends on access_token, _http, and _body.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 5 (action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 233–236)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked response body. It is the shared path for API calls that create tokens or run actions.

**Data flow**: It receives an API path and a dictionary body. It gets an access token, opens an HTTP client with that token, sends the body as JSON, checks the response through _body, and returns the resulting dictionary.

**Call relations**: connect_token and run_action call this for their write-style Pipedream operations. Like _get, it centralizes token use, temporary HTTP client creation, and response validation.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 238–251)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates a temporary async HTTP client configured for Pipedream. For authenticated calls, it adds the bearer token and Pipedream environment header.

**Data flow**: It receives an optional access token. If a token is present, it builds headers containing authorization and environment information; if not, it uses no special headers. It returns an httpx AsyncClient with the Pipedream base URL, timeout, optional test transport, and those headers.

**Call relations**: access_token uses this without a token for the OAuth token request. _get and _post use it with a token for normal authenticated Connect API calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 254–255)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This avoids crashes when Pipedream returns an unexpected shape.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Account-reading code uses this before looking inside nested response data. It supports connected_account, workspace_account, newest_account, and _account.

*Call graph*: called by 4 (connected_account, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 258–271)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Validates that an account record is healthy and belongs to one exact expected external user. This is a key guard against using someone else’s connected account.

**Data flow**: It receives a raw account record, account ID, and expected external user ID. It first converts and checks the record with _account, compares the account’s owner to the expected owner, raises a 403 error if they differ, and returns the ConnectedAccount if they match.

**Call relations**: connected_account and newest_account call this after fetching account data from Pipedream. It builds on _account and uses PipedreamError to stop unsafe ownership mismatches.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 274–286)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into a small local ConnectedAccount, while checking basic safety facts. It refuses records with no owner or accounts marked unhealthy.

**Data flow**: It receives a record and account ID. It reads the external owner, checks that it is a non-empty string, rejects the account if Pipedream says it is unhealthy, extracts the app slug if present, and returns a ConnectedAccount containing the account ID, app, and owner.

**Call relations**: workspace_account uses this before workspace ownership checks, and _owned_account uses it before exact-user ownership checks. It calls _dict to safely read the nested app information.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 289–290)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard beginning of external user IDs for one workspace. This gives the code a consistent naming rule for workspace-owned Pipedream connections.

**Data flow**: It receives a workspace UUID and returns a string made from the fixed prefix, the workspace ID in compact hexadecimal form, and a trailing underscore.

**Call relations**: _workspace_owns_external_user uses this to recognize valid workspace users, and connection_user_id uses it when creating a new state-scoped external user ID.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 293–302)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user ID belongs to a given workspace. It supports both an older direct workspace format and the newer format that includes a derived connection ID.

**Data flow**: It receives a workspace UUID and an external user ID. It first accepts the legacy exact workspace ID format. Otherwise it checks for the workspace prefix, extracts the remaining connection ID, and accepts it only if it is exactly the expected length and made only of lowercase hexadecimal characters.

**Call relations**: workspace_account calls this after reading an account’s owner. It uses workspace_user_prefix so the validation rule matches the rule used when generating connection user IDs.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 305–307)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user ID for one workspace and one connection state value. This lets a consent flow be tied to a specific workspace-scoped connection without storing a provider secret.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first fixed number of hexadecimal characters as a connection ID, adds it after the workspace prefix, and returns the full external user ID.

**Call relations**: This complements _workspace_owns_external_user: one function creates IDs in the approved format, and the other later verifies that an account owner matches that format.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 310–318)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe dictionary or raises a clear error. It prevents callers from accidentally treating an error page, empty failure, or non-object JSON as valid data.

**Data flow**: It receives an httpx Response. If the status code means failure, it raises PipedreamError with the status and text. If the response has no content, it returns an empty dictionary. Otherwise it parses JSON, verifies the parsed value is a dictionary, and returns it.

**Call relations**: access_token, _get, and _post all pass their raw HTTP responses through this helper. This means every Pipedream API call in the client shares the same response validation behavior.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 321–339)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a PipedreamClient from environment variables for the current deployment. It fails loudly if the required Pipedream project credentials are missing.

**Data flow**: It reads the client ID, client secret, project ID, and optional environment name from process environment variables. If any required value is missing, it raises RuntimeError. Otherwise it returns a PipedreamClient configured with those values.

**Call relations**: Higher-level code can call this when it needs the deployment’s real Pipedream client. It is the setup doorway into the client class, while the instance methods perform the actual consent, account, catalog, and action calls.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling`

Some connected services, such as Gmail or other providers, require private credentials. In this setup, Pipedream keeps those credentials on its own servers and does not hand them to this application. This file solves that gap by acting like a mail-forwarding service: the connector writes a normal request to the provider, and this transport repackages it so Pipedream can send it onward with the right credential added server-side.

The main class, PipedreamProxyTransport, plugs into httpx, an HTTP client library. When a request is about to be sent, it reads the original request body, gets a Pipedream access token, and builds a new request to Pipedream's proxy endpoint. The original provider URL is encoded into the proxy path, and the Pipedream account and user identifiers are placed in the query string.

Headers need special care. Pipedream only forwards headers that start with x-pd-proxy-, so this file adds that prefix to useful provider headers. It deliberately drops transport-level headers such as host, content-length, and authorization, because forwarding those could be wrong or unsafe.

The important result is transparency: the connector still sees the provider's real status code, headers, and body. That means existing logic that depends on responses like “404 means cursor expired” or “401 means permission problem” continues to work.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the core request-rewriting step. It takes a normal HTTP request meant for a provider, wraps it as a Pipedream Connect Proxy request, and sends that instead so Pipedream can add the real provider credential.

**Data flow**: A provider request comes in with a method, URL, headers, and body. The function asks the Pipedream client for an access token, reads the request body, filters and prefixes headers that should be passed through, encodes the original provider URL safely for use inside a path, and builds a new request to Pipedream's proxy endpoint. It then sends that new request through the inner transport and returns the response it gets back, preserving the provider-like result for the caller.

**Call relations**: When httpx uses this transport to send a request, this method is the point where the request is rerouted through Pipedream. It uses httpx.Request.aread to collect the body, base64.urlsafe_b64encode to make the original URL safe to place in the proxy path, httpx.URL to build the proxy address, and httpx.Request to create the outgoing proxy request before handing it to the wrapped inner transport.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This shuts down the wrapped HTTP transport when the proxy transport is no longer needed. It makes sure any underlying network resources are cleaned up properly.

**Data flow**: Nothing new is created from the input. The function simply forwards the close request to the inner transport, which releases its own open connections or other resources.

**Call relations**: When the HTTP client is being closed, this method lets PipedreamProxyTransport take part in the normal cleanup chain. Rather than doing its own network cleanup, it hands the shutdown step to the inner transport that actually performed the sending.


### Composio connector bridge
Composio integration files resolve toolkits, search tools, run third-party app actions, and support remote MCP-based tool lookup.

### `extensions/composio/ufo_ext_composio/broker.py`

`domain_logic` · `request handling and connector tool execution`

ComposioBroker is the shared adapter used by Composio-based connectors. Think of it like a reception desk: the rest of the app asks for “tools for this provider,” “the schema for this tool,” or “run this tool,” and this file translates those requests into Composio API calls.

A key safety rule here is that the broker does not store a long-lived client inside itself. Each method asks for the current Composio client when it runs. That matters for tests and for runtime configuration, because a different network transport or API key can be used without stale state leaking between calls.

The file also improves error messages in ways that help an agent recover. If a tool slug is wrong, it tries to fetch real available slugs and adds them to the error. If an account grant is stale, meaning the stored connected account no longer belongs to this workspace’s broker user, it tells the user to reconnect instead of pretending the problem is a missing tool.

Files are treated specially. Tool outputs are searched for Composio file objects containing names and temporary download URLs. Uploads are staged by asking Composio for a place to put bytes, then returning the argument shape the tool expects. Credentials are also guarded: before returning a proxy transport, the broker confirms the account belongs to this workspace user, so provider secrets are never handed directly to feed-sync code.

#### Function details

##### `ComposioBroker.tools`  (lines 48–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds tools offered by a Composio provider that match a search query. It returns them in UFO’s common BrokerTool shape so the rest of the system does not need to understand Composio’s raw response format.

**Data flow**: It receives a workspace id, a provider name, and a search query. It asks the current Composio client to list matching tools for that provider, then passes the raw listing through _discovered_tools to keep only usable slugs and short descriptions. It returns a tuple of BrokerTool objects.

**Call relations**: This is called when the system needs to show or discover available connector tools. It relies on composio_client for the live API connection and hands the raw result to _discovered_tools for cleanup.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–65)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the input schema for one Composio tool. A schema is the recipe that says what arguments the tool accepts, and this method converts Composio’s version into the shape UFO expects.

**Data flow**: It receives a workspace id, provider name, and tool slug. It asks Composio for that tool’s schema; if Composio says the slug does not exist, it raises UnknownBrokerTool. Otherwise it rewrites any file-upload input fields into UFO’s workspace-file vocabulary and returns a BrokerTool containing the slug, description, and input schema.

**Call relations**: This is used after a tool has been selected and the system needs to know how to call it. It uses composio_client to fetch the raw schema, workspace_file_schema to translate file inputs, and BrokerTool as the common return object.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 67–90)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on behalf of this workspace’s broker user. It also turns two common failures into more helpful guidance: stale account grants and misspelled or outdated tool slugs.

**Data flow**: It receives the workspace id, provider, tool slug, arguments, connected account id, and an optional idempotency key, which helps avoid duplicate execution. It builds the Composio external user id from the workspace id and sends the execution request. On success, it returns Composio’s response dictionary. On failure, it checks whether the account looks stale, whether the slug was missing, or whether the original error should simply be re-raised.

**Call relations**: This is the main tool-running path. It calls _stale_account to recognize dead connected-account errors, _reconnect_error to produce user-facing reconnect guidance, and _slug_miss to enrich missing-tool errors with available slugs.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 92–97)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a tool execution response. This matters because Composio can bury file objects inside nested dictionaries or lists rather than placing them in one fixed field.

**Data flow**: It receives the full tool response dictionary. It creates an empty list, asks _collect_files to walk through the whole response, and returns every discovered file as BrokerFile objects in a tuple.

**Call relations**: This is used after execute returns, when the system wants to expose generated files to the caller. It delegates the recursive searching to _collect_files.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 99–115)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a file upload for a tool input. Instead of sending the file through the tool call itself, it asks Composio for a temporary upload location and returns the exact argument the later tool call should use.

**Data flow**: It receives workspace id, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL to PUT the file to, the content type to use, and the small argument object containing the file name, MIME type, and Composio storage key.

**Call relations**: This is called before executing a tool that needs a file input. It depends on the current Composio client to create the upload slot and wraps the result in UFO’s StagedUpload model.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 117–120)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio tools using Composio’s Tool Router rather than simple provider listing. This gives the system a richer search result when it needs help finding the right tool for a user request.

**Data flow**: It receives workspace id, provider, and query. It gets the current Composio client and passes all of that to search_connector_tools. The result is returned as a BrokerSearch object.

**Call relations**: This supports discovery flows where the agent searches for a useful connector action. It is mostly a pass-through to Composio’s search helper, using the current client so transport overrides still apply.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 122–138)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a safe Credential object that can make provider HTTP requests through Composio’s proxy, without revealing the provider’s real token. Before doing that, it checks that the requested connected account belongs to this workspace’s broker user.

**Data flow**: It receives workspace id, provider, and connected account id. It builds the broker user id, asks Composio to confirm that the account is connected for that user and provider, and turns a not-found response into reconnect guidance. If the account is valid, it builds a ComposioProxyTransport using the Composio API base, API key, account id, and an HTTP transport, then wraps that transport in a Credential.

**Call relations**: This is used when code needs authenticated provider access but should not receive raw secrets. It calls _reconnect_error for stale or missing grants and hands network traffic to ComposioProxyTransport.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 140–161)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by adding real tool slugs available for the provider, when it can find them. This helps an agent recover by trying a valid slug next time.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the bad slug into a simple search query, asks Composio for matching tools, and if needed tries a blank query as a fallback. If discovery works and finds tools, it returns a new ComposioError whose message includes the available slugs; otherwise it returns the original error unchanged.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a missing tool slug. It uses _discovered_tools to clean up list_tools results and creates a replacement ComposioError only when the extra information is trustworthy.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 164–173)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through a tool response and collects every Composio file object it finds. A Composio file object is recognized by having a non-empty s3url plus a name and MIME type.

**Data flow**: It receives any value from the response tree and a list that is being filled. If the value looks like a file object, it appends a BrokerFile with the file name and URL. If the value is a dictionary or list, it recursively checks each child. It does not return a separate value; it changes the provided list.

**Call relations**: ComposioBroker.file_outputs starts this walk after a tool finishes. _collect_files does the detailed searching and creates BrokerFile objects for the outer method to return.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 176–187)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error means the connected account grant is stale or missing. It is intentionally strict so ordinary provider errors are not mistaken for account-reconnection problems.

**Data flow**: It receives a ComposioError and the connected account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account: Composio’s own phrase “connected account” with “not found,” or the exact account id with “not found.” It returns true if the error matches those patterns, otherwise false.

**Call relations**: ComposioBroker.execute calls this before treating a 404 as a missing tool. That ordering is important because a dead account should lead to reconnect guidance, not a list of tool slugs.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 190–191)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a ComposioError that keeps the original failure text but adds clear guidance telling the user to reconnect the provider account. This makes stale grants understandable to an agent or end user.

**Data flow**: It receives the original ComposioError and provider name. It asks stale_grant_guidance for provider-specific reconnect wording, appends that wording to the original error body, and returns a new ComposioError with the same status code.

**Call relations**: ComposioBroker.execute uses this when a tool run points to a stale account, and ComposioBroker.credential uses it when account ownership verification fails. It centralizes the reconnect message so both paths explain the same fix.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 194–216)

```
def _discovered_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Turns Composio’s raw list_tools response into UFO’s simpler BrokerTool list. It filters out malformed entries and keeps only a usable slug plus a short description.

**Data flow**: It receives the raw listing dictionary from Composio. It reads the items field, ignores it if it is not a list, then loops through each dictionary item. For each valid item, it chooses slug or name as the tool slug, trims long descriptions to a fixed cap, builds a BrokerTool, and finally returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal discovery, and ComposioBroker._slug_miss uses it when enriching a missing-tool error. It is the small translator between Composio’s catalog format and UFO’s broker format.

*Call graph*: called by 2 (_slug_miss, tools); 1 external calls (__init__).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector setup, tool discovery, and tool execution`

Composio acts like a switchboard for external services. Instead of this project storing a separate password or access token for every app, Composio keeps those secrets and exposes safe API calls. This file is the client that talks to Composio’s web API.

The main class, ComposioClient, knows how to do the important steps in a connector’s life. First, it can create a consent link so a user can approve access to a service. Later, it can confirm that the returned connected account belongs to the right workspace user, is active, and is for the expected toolkit. It can also list and describe tools, execute a selected tool on Composio’s servers, and ask Composio for a temporary upload location when a tool needs a file.

The file also decides which Composio toolkits are safe and useful to offer. Some are rejected because they have no usable tools, lack Composio-managed login support, or are explicitly banned because the live catalog cannot support a complete workflow.

For tool discovery, it can open a cached Tool Router session and ask Composio’s semantic search tool for likely tools, suggested steps, and warnings. Think of this as asking the switchboard not only “which extension can do this?” but also “what should I watch out for?”

#### Function details

##### `connectable`  (lines 120–144)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit is actually usable by this deployment. It keeps the system from offering a service that cannot be logged into, has no tools, or is known to be incomplete for real work.

**Data flow**: It receives a toolkit slug, such as a service name, and a catalog record from Composio. It checks the slug against the local banned list, then checks the catalog record for managed authentication support and a positive tool count. It returns true only when all of those checks pass.

**Call relations**: ComposioClient.connectable_toolkit uses this when checking one requested toolkit, and ComposioClient.list_toolkits uses it while browsing search results. In both cases, this function is the gatekeeper before the service is shown or accepted.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 151–154)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error when Composio fails or sends a response this client cannot safely use. It records both the HTTP status number and the response body so the failure is visible instead of being silently ignored.

**Data flow**: It receives a status code and a body string. It formats them into a readable runtime error message and stores the two pieces of information on the error object. The output is an exception that can be raised by callers.

**Call relations**: Many client methods create this error when Composio returns bad data, refuses a request, or gives an unexpected shape. _body uses it for low-level HTTP failures, while higher-level methods use it when required fields like redirect URLs or upload keys are missing.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 171–180)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to authorize a connector. This is the start of the OAuth consent flow, where OAuth means a standard way for a user to grant access without giving this project their password.

**Data flow**: It receives a toolkit name, a Composio user id, and a callback URL. It first finds or creates an auth configuration, then posts those details to Composio. It expects Composio to return a redirect URL and returns that URL; if the URL is missing, it raises an error.

**Call relations**: This method begins by calling _auth_config because Composio needs to know which login configuration to use. It then sends the request through _post. If Composio’s answer is unusable, it raises ComposioError so the caller does not hand the user a broken link.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 182–206)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Checks that a connected account returned by Composio is valid for the current workspace and connector. This prevents someone from reusing an account id that belongs to another user or another service.

**Data flow**: It receives an account id, the expected user id, and the expected toolkit slug. It fetches the account from Composio, checks the owner, checks that the account is active, and checks that it authenticates the requested toolkit. If all checks pass, it returns an OAuthAccount containing the account id; otherwise it raises an error.

**Call relations**: It uses _get to read Composio’s account record. It raises ComposioError for ownership, status, or toolkit mismatches. When the record is acceptable, it hands back an OAuthAccount for the rest of the connector system to store as the grant reference.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.list_tools`  (lines 208–214)

```
async def list_tools(self, toolkit: str, query: str='', limit: int=TOOL_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Asks Composio for tools available inside one toolkit, optionally filtered by a search query. This lets the broker discover tool slugs rather than keeping a fixed list in this codebase.

**Data flow**: It receives a toolkit slug, an optional query, and a limit. It builds URL parameters from those values and sends a GET request to Composio’s tools endpoint. It returns Composio’s response as a dictionary.

**Call relations**: It is a small public wrapper around _get. ComposioBroker._slug_miss calls it when a tool slug was not already known and the broker needs to check Composio’s catalog.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 216–217)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed schema for one Composio tool. A schema describes what inputs the tool expects, like a form telling the agent which fields to fill in.

**Data flow**: It receives a tool slug. It requests the matching tool detail endpoint from Composio and returns the response dictionary. It does not reshape the result itself.

**Call relations**: This method delegates the network work to _get. It is used by higher-level connector code when it needs exact input details for a selected tool.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 219–236)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a single toolkit slug is safe and useful to offer, and returns its human-friendly name if it is. It also rejects slugs with unsafe characters before they are placed into a URL.

**Data flow**: It receives a slug string. It first checks that the slug only contains expected toolkit characters, then fetches the toolkit record from Composio. If Composio says it does not exist, or if connectable rejects it, the result is None. Otherwise it returns the toolkit’s display name, falling back to the slug.

**Call relations**: This method calls _get to read the toolkit details and connectable to apply the project’s local policy. It is typically used when resolving whether a member-requested connector name can be claimed by the Composio extension.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 238–256)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio’s catalog for toolkits this deployment can actually connect. It powers discovery without showing services that would fail later.

**Data flow**: It receives a search query and a result limit. It asks Composio for matching toolkit records, skips malformed or unusable entries, applies connectable to each one, and returns a tuple of slug-and-label pairs.

**Call relations**: It uses _get to fetch catalog search results and connectable as the filter. The result is a clean list that user-facing discovery code can show without separately understanding Composio’s raw catalog format.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 258–272)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio’s server side for a given user and optional connected account. This is important because Composio injects the stored access token there, so the token never needs to enter this project.

**Data flow**: It receives a tool slug, argument values, a user id, and optionally a connected account id and idempotency key. It builds the request body, checks that the serialized argument payload is not too large, adds an idempotency header if provided, and posts the request. It returns Composio’s execution response.

**Call relations**: It sends the final request through _post. json.dumps is used only to measure the request size before sending. Callers use this after discovery and account validation, when the agent is ready to perform the chosen action.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 274–298)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a temporary place to upload a file needed by a tool. This keeps file staging aligned with Composio’s expectations without this client carrying the actual file bytes.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts those details to Composio’s upload-request endpoint. It expects a storage key and may also receive a presigned upload URL; it returns a ComposioUpload with those values, or raises an error if they are missing or malformed.

**Call relations**: It uses _post for the API call and ComposioError for bad response shapes. When successful, it creates a ComposioUpload object that other sandbox or execution code can use to upload bytes and then refer to the staged file.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 300–311)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. A Tool Router session is a temporary search endpoint scoped to a user and one or more toolkits.

**Data flow**: It receives a user id and a list of toolkit slugs. It posts a session request to Composio, then extracts the session id and MCP URL, where MCP is the protocol endpoint used to call the router’s search tool. It returns a ToolRouterSession or raises an error if the response lacks the needed fields.

**Call relations**: search_connector_tools calls this when no cached session exists for a user and connector. This method sends the request through _post and returns the session details needed for later mcp_session.mcp_call_tool calls.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 313–331)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the login configuration Composio should use for a toolkit, creating a managed one if none exists. This lets operator-created custom configs take priority while still allowing most connectors to work out of the box.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts an id if present. If none is found, it posts a request to create a Composio-managed auth config, then reads the new id. It returns the auth config id or raises an error if creation succeeds without an id.

**Call relations**: connect_link calls this before creating an OAuth link. Internally it uses _get, _post, and _auth_config_id, and raises ComposioError when Composio’s response cannot support a real consent flow.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 333–335)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs one authenticated GET request to Composio and turns the response into a dictionary. It is the shared read path for the client.

**Data flow**: It receives a path and optional query parameters. It opens an HTTP client, sends the GET request, passes the HTTP response to _body, and returns the parsed dictionary. The temporary HTTP client is closed afterward.

**Call relations**: Most read-style methods call _get, including account checks, toolkit lookup, tool listing, tool schema fetching, and auth config lookup. It relies on _http to create the configured HTTP client and _body to validate the response.

*Call graph*: calls 2 internal fn (_http, _body); called by 6 (_auth_config, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 337–341)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs one authenticated POST request to Composio and turns the response into a dictionary. It is the shared write or action path for the client.

**Data flow**: It receives a path, a JSON body, and optional headers. It opens an HTTP client, sends the POST request with that body, passes the response to _body, and returns the parsed dictionary. The HTTP client is closed after the request.

**Call relations**: Methods that create links, create auth configs, execute tools, mint uploads, and open router sessions all call _post. Like _get, it uses _http for setup and _body for response checking.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 343–349)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds a configured asynchronous HTTP client for Composio. An asynchronous client can wait on network responses without blocking the whole program.

**Data flow**: It reads the Composio API key and optional test transport stored on the ComposioClient. It creates an httpx.AsyncClient with the base URL, API-key header, timeout, and transport. The returned client is then used by _get or _post.

**Call relations**: _get and _post call this each time they make a request. Creating the client here keeps all Composio requests using the same base address, authentication header, timeout, and test override behavior.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 352–360)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Checks and parses a raw HTTP response from Composio. It is the last safety checkpoint before higher-level code trusts the response.

**Data flow**: It receives an httpx response. If the status code is an error, it raises ComposioError with the status and text. If the response is empty, it returns an empty dictionary. Otherwise it parses JSON and requires it to be an object-like dictionary; non-dictionary JSON also becomes an error.

**Call relations**: _get and _post both pass every Composio response through _body. This means the rest of the client can work with dictionaries and does not need to repeat HTTP error handling.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 363–389)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio tool input schemas so file inputs are described in the project’s own workspace-file language. This tells the agent to provide a local /workspace path instead of raw Composio storage details.

**Data flow**: It receives any schema value, such as a dictionary, list, or simple value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object requiring the workspace file path. For nested dictionaries and lists, it recursively rewrites their contents. Other values pass through unchanged.

**Call relations**: _search_result calls this while turning Tool Router search results into BrokerTool records. It bridges Composio’s internal upload representation and the simpler file input format exposed to the agent.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 392–399)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first auth configuration id from a Composio list response. It is a small helper that keeps _auth_config focused on the larger decision of reuse versus create.

**Data flow**: It receives a response dictionary. It looks for an items list and returns the first string id found inside a dictionary item. If the shape is missing or no id is found, it returns None.

**Call relations**: _auth_config calls this after fetching existing auth configs. A returned id lets _auth_config reuse the existing setup; None tells it to create a new managed config.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 402–409)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the default ComposioClient for this deployment using the API key from the environment. It fails loudly if the key is missing because connector login cannot work without broker access.

**Data flow**: It reads the COMPOSIO_API_KEY environment variable. If the value is absent or empty, it raises a runtime error. Otherwise it returns a ComposioClient initialized with that key.

**Call relations**: Higher-level code can call this when it needs the real Composio client rather than a test client. It centralizes the environment-variable lookup so callers do not each need to know the exact key name.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 416–417)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This prevents malformed Composio search results from causing type errors while being parsed.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: _search_result uses this repeatedly while walking nested Tool Router output. It acts like a guardrail around data that comes from an external service.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 420–423)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts a tuple of non-empty strings from a list-like value. It filters away missing, empty, or non-string entries from external data.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only entries that are real non-empty strings and returns them as a tuple.

**Call relations**: _search_result uses this to read tool slugs, recommended plan steps, guidance, and pitfalls from Tool Router results. It keeps the parser tolerant of unexpected shapes while still returning clean text.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 426–460)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router search output into the project’s BrokerSearch format. It turns a rich but Composio-specific response into the simpler shape the dynamic connector tools know how to display and use.

**Data flow**: It receives a dictionary returned by the Tool Router. It finds tool schemas and result entries, collects primary and related tool slugs without duplicates, rewrites any file-uploadable schemas into workspace-file schemas, and gathers plan steps, execution guidance, and known pitfalls. It returns a BrokerSearch containing BrokerTool entries plus the supporting advice.

**Call relations**: search_connector_tools calls this after mcp_session.mcp_call_tool returns. Inside, it uses _dict and _str_tuple to safely read external data and workspace_file_schema to adapt file inputs for this project.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 463–486)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for useful Composio tools inside one connector using Composio’s semantic Tool Router. Instead of only matching exact names, it asks Composio what tools fit the user’s use case and returns suggested tools plus advice.

**Data flow**: It receives a ComposioClient, workspace id, connector slug, and natural-language query. It builds the Composio user id for that workspace, reuses a cached router session if one exists, or creates one under a lock so concurrent searches do not open duplicates. It calls the router’s search tool through MCP, then converts the result into BrokerSearch.

**Call relations**: When a session is missing, it calls ComposioClient.tool_router_session. It then hands the query to ufo_ext_composio.mcp_session.mcp_call_tool and passes the raw result to _search_result. This is the high-level path for semantic discovery; actual tool execution still goes through ComposioClient.execute_tool, not through the router.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio’s Tool Router. Composio exposes tool search through an MCP endpoint. MCP, or Model Context Protocol, is a shared communication format for calling tools from AI-related systems. Think of this file like a clerk who briefly opens a service window, asks one question, writes down the answer in a simple form, and then closes the window.

The main job is to call a named MCP tool, usually Composio’s semantic tool search tool, over a streamable HTTP connection. “Streamable HTTP” means the response can arrive over an HTTP connection in a way suited for longer or structured tool conversations, rather than just a simple web request.

After the remote call returns, the file carefully normalizes the answer. Some MCP results already include parsed structured data, which is the best case. If that is missing, it tries another structured field. If that is also missing, it looks for a text block and tries to read it as JSON, which is a common plain-text format for structured data. If even that is not JSON, it preserves the text so the caller still gets something useful.

A notable design choice is that this file only supports search through the Tool Router. Actual tool execution happens elsewhere through Composio’s execute API, so billing, permissions, and grants stay tied to the correct Composio flow.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one named tool on a Composio MCP endpoint and returns the result as a plain dictionary. Someone would use this when they want the rest of the code to receive predictable Python data, without caring about the different shapes an MCP response might use.

**Data flow**: It receives the endpoint URL, the tool name, the tool arguments, HTTP headers, and a timeout. It opens a temporary MCP client connection to that endpoint, sends the tool call with the provided arguments, then closes the connection. It first returns parsed dictionary data if the response already has it. If not, it tries another structured response field. If that is missing too, it looks through text content, tries to parse the first text block as JSON, and wraps non-dictionary values so the caller still receives a dictionary. If there is no usable content, it returns the raw data under a result key.

**Call relations**: This function is the file’s single public action. When another part of the extension needs to ask Composio’s Tool Router for a tool-search result, it calls this function. Inside, it relies on FastMCP’s Client and StreamableHttpTransport to do the network conversation, and on json.loads to convert text-form JSON into normal Python data when the MCP response did not already provide structured data.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/resolver.py`

`domain_logic` · `connector discovery and connection setup`

Composio offers many third-party toolkits, and this project cannot realistically list each one as a separate connector ahead of time. This file solves that by acting like an “open front desk” for Composio: when someone asks for a provider name, it asks Composio whether that toolkit really exists and can be connected, then prepares the system to use it.

The main piece is `ComposioResolver`. It is small and mostly stateless, meaning it does not keep its own long-lived client connection. Instead, it asks for the Composio client each time it needs one. That matters for tests and for safe runtime behavior, because client settings or mocked network transports can be swapped without stale connections hanging around.

The resolver does four practical jobs. It says which file-transfer hosts are trusted for Composio tool results. It checks whether a requested toolkit slug, such as a service name, is valid and connectable. It builds an OAuth provider description, which is the information the system needs to start an authorization flow; here the host is blank because the actual account token stays with Composio and tools run through Composio. Finally, it creates catalog entries for search results so discovery only suggests toolkits the user can actually connect later.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Returns the list of Composio file-store hosts that the system should trust for file transfers. This allows files produced or needed by Composio-run tools to pass through the sandbox safely.

**Data flow**: It receives no extra input beyond the resolver instance. It reads the shared `COMPOSIO_TRANSFER_HOSTS` setting and returns it unchanged as a tuple of host names.

**Call relations**: Other connector or sandbox code can ask the resolver which remote hosts are allowed when a Composio-backed tool moves files in or out. This property does not call out to Composio; it simply exposes the known allowed hosts.


##### `ComposioResolver.claims`  (lines 35–36)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether a requested provider name belongs to a Composio toolkit this deployment can broker. In plain terms, it asks Composio, “Is this service connectable?”

**Data flow**: It takes a provider slug, asks the current Composio client to look up that toolkit, and turns the answer into a yes-or-no result. If Composio returns toolkit information, the result is `True`; if nothing is found, the result is `False`.

**Call relations**: During connector resolution, the wider registry can call this after explicitly registered connectors have had their chance. This function gets a fresh Composio client through `ufo_ext_composio.client.composio_client` and uses that client to confirm whether this open Composio namespace should accept the provider.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 38–39)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds the OAuth connection description for a validated Composio toolkit. OAuth is the common web sign-in-and-permission flow used to let an app access another service without sharing a password.

**Data flow**: It receives the provider slug and creates a `ComposioOAuthProvider` for that slug. The host field is set to an empty string because this system is not connecting directly to the provider’s server; Composio keeps the account token and performs tool work on the server side.

**Call relations**: Once a provider has been accepted, the connection flow can call this to get the provider description it needs to start authorization. It hands off to `ComposioOAuthProvider.__init__` to package that provider information in the expected form.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 41–44)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Creates the connector entry that tells the system how to route a Composio toolkit request. It gives the provider a human-readable label and attaches the shared Composio broker that will execute work for it.

**Data flow**: It takes a provider slug, turns underscores into spaces, title-cases it for display, and combines that label with the resolver’s broker. The result is a `ConnectorEntry` representing this toolkit inside the connector system.

**Call relations**: After the resolver has claimed a provider, the registry or connection machinery can ask for this entry so future actions know where to go. This function constructs a `ConnectorEntry`, pointing all dynamic Composio toolkit names back to the same shared broker.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 46–48)

```
async def catalog(self, query: str, limit: int=TOOL_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches Composio’s toolkit catalog and returns search results in the project’s standard catalog-entry shape. This powers discovery, so users can find services without the project hard-coding every Composio toolkit name.

**Data flow**: It receives a search query and an optional maximum number of results. It asks the current Composio client for matching toolkits, then converts each returned slug and label into a `CatalogEntry`. The output is a tuple of catalog entries ready for the rest of the system to display or use.

**Call relations**: Discovery tools call this when a user searches for connectable services. It gets a fresh client through `ufo_ext_composio.client.composio_client`, asks that client for toolkit rows, and wraps each row with `CatalogEntry.__init__` so the rest of the connector catalog sees a normal result format.

*Call graph*: 2 external calls (__init__, composio_client).


### Connector orchestration and MCP
These files expose generic connector tools, provide an evaluation connector environment, and let workspaces call tools from configured MCP servers.

### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `tool invocation / request handling`

A connector broker is like a service desk for many outside apps, such as GitHub, Slack, or Gmail. The agent cannot keep a hard-coded tool for every possible outside action, so this file gives it a small search-and-run interface instead. First, it can search for connectors. Then it can ask a chosen connector what real tool names and input shapes are available. Finally, it can execute one tool through the broker using a connected account.

The careful parts are around files and large results. If an argument says it refers to a workspace file, the file is checked, size-limited, uploaded from inside the sandbox, and replaced with the broker's own file reference. If a connector returns files, they are downloaded into a dedicated workspace folder. If a connector puts base64 data directly in JSON, this file turns small text into readable text and writes large or binary data into workspace files. Base64 is an encoded form of bytes; left as-is, it is huge and not useful for a language model to read.

The file also shrinks repeated JSON objects. If the same large object appears again and again, later copies become a pointer to the first copy. This preserves the facts while keeping results small enough to stay readable in context.

#### Function details

##### `list_external_tools`  (lines 147–170)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches the current turn's connector registry for available outside services, such as GitHub or Slack. It is used before looking for specific actions, because the set of available connectors comes from the live registry rather than a fixed list.

**Data flow**: It receives the tool context and search queries. It reads the connector registry from the context, checks local connector names and labels, then also asks broker catalogs for matching connectors in parallel. It returns a JSON tool result containing matching connector IDs and labels.

**Call relations**: This is one of the public connector tools. It starts by using _registry to get the live connector registry, uses asyncio.gather to ask catalog searches at the same time, and finishes by handing the response to _json_result so the tool framework receives ordinary text JSON.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 173–195)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes the real tools available inside one connector. It helps the agent avoid guessing tool names by returning either full schemas for known tool names or a discovery list for a search query.

**Data flow**: It receives a connector ID, optional exact tool names, and an optional query. It looks up the connector entry, asks the broker for schemas for exact names, records any names the broker does not recognize, and may ask the broker for a list of matching tools. It returns JSON with schemas, available tool slugs, and unresolved names when needed.

**Call relations**: This is normally called after list_external_tools and before call_external_tool. It gets the registry through _registry, formats each broker tool with _tool_json, builds fallback search text with _discovery_query when names do not resolve, and wraps the final payload with _json_result.

*Call graph*: calls 4 internal fn (_discovery_query, _json_result, _registry, _tool_json).


##### `call_external_tool`  (lines 198–202)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real tool on an external connector using a connected account. It is the gateway from the agent's tool call into the broker's server-side execution API.

**Data flow**: It receives the chosen connector, tool name, optional account ID, and arguments. It looks up the connector, resolves which connected account should be used, creates a _ConnectorCall object, and asks it to run the execution. It returns a tool result whose text is the processed JSON response from the connector.

**Call relations**: This is the execution endpoint after a tool has been discovered and described. It uses _registry to find the connector, calls ToolContext.connector_account to bind the request to an account, then delegates the complex staging, execution, file fetching, decoding, and shrinking flow to _ConnectorCall.run.

*Call graph*: calls 2 internal fn (connector_account, _registry); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 230–243)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Carries out one connector tool execution from start to finish. It prepares file arguments, calls the broker, brings returned files back into the workspace, cleans up encoded data, and shrinks repeated result objects.

**Data flow**: It receives raw tool arguments and a connected account ID. It walks the arguments and stages any workspace files, sends the staged arguments to the broker execute call, fetches broker-declared output files, translates base64-like result content into readable text or workspace file references, adds the fetched file list when present, and returns a JSON string with duplicate objects condensed.

**Call relations**: call_external_tool hands off to this method for the real work. Inside the flow it calls _staged_value before execution, _fetched_files and _translated_node after execution, then moves the duplicate-condensing work into a worker thread with asyncio.to_thread so the main async loop is not held up.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 245–260)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks an argument value and replaces any workspace-file marker with a broker-ready file reference. This lets nested arguments contain files without each caller having to know the upload protocol.

**Data flow**: It receives any value from the arguments tree. If the value is exactly a dictionary containing the workspace file key, it validates the path and stages that file. If the value is a dictionary or list, it recursively processes children. Other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before broker execution. When it finds a real workspace file marker, it hands the path to _stage_file; otherwise it keeps walking the structure itself.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 262–293)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker's file store, or skips upload if the broker already has the same file. It turns a local workspace path into the argument value the external tool expects.

**Data flow**: It receives a workspace path. It scopes the path to the workspace, runs a sandbox command to compute the file's MD5 hash and size, rejects unreadable or oversized files, guesses the file type from its name, asks the broker for an upload location, and if needed uploads the bytes with curl from inside the sandbox. It returns the broker's argument object for the staged file.

**Call relations**: _staged_value calls this whenever it sees a workspace_file argument. It relies on workspace_path, shell quoting, MIME type guessing, and sandbox bash commands so the file transfer happens inside the sandbox rather than through the main server process.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 295–314)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. This gives the agent stable workspace paths for files that came from the external service.

**Data flow**: It receives broker file records, each with a name and a presigned download URL. For each one, it chooses a safe filename, creates a unique connector_files subdirectory, and uses curl inside the sandbox to download the file with a size limit. It returns a list of file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker's file_outputs view of the response. It uses path cleanup, unique IDs, and shell quoting so each fetched file lands safely and does not overwrite another.

*Call graph*: called by 1 (run); 3 external calls (PurePosixPath, quote, uuid4).


##### `_ConnectorCall._translated_node`  (lines 316–376)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Processes one JSON object from a connector result and translates provider-marked base64 fields into something readable. It keeps ordinary data untouched and only trusts explicit markers such as an encoding field saying base64.

**Data flow**: It receives a mapping from the result and a recursion depth. It first walks child values, then looks for marker fields saying the node contains base64 content. For valid base64 fields, it decodes bytes, chooses a name and MIME type, and turns the bytes into inline text or a workspace file reference. It updates the marker to say whether content was inlined or offloaded when all marked fields were translated.

**Call relations**: _ConnectorCall.run calls this on the broker response, and _translated calls it again for nested objects. It uses _decoded_base64 to safely decode marked fields, _translated_bytes to decide text versus file, and _translated to continue through child structures.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 378–397)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Walks any value inside a connector result and applies the same translation rules to nested objects, lists, and data URLs. It also stops at a depth limit so strange or deeply nested broker output does not break the call.

**Data flow**: It receives a value and the current depth. If the depth is too high, it returns the value unchanged. For dictionaries it delegates to _translated_node, for lists it processes each item, and for short data URL strings it tries data URL translation. Other values pass through as they came.

**Call relations**: _translated_node uses this to walk every child in a result object. When it finds a dictionary it loops back to _translated_node, and when it finds a possible data URL it hands it to _translated_data_url.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 399–411)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Turns a base64 data URL into readable text or a workspace file reference. A data URL is a string that carries both a media type and the encoded bytes inside the string itself.

**Data flow**: It receives a string beginning with data:. It checks whether the string really matches the expected base64 data URL shape, decodes the payload if valid, chooses a filename extension from the declared MIME type, and passes the bytes onward for inline-or-file handling. If the string is not a valid data URL, it returns it unchanged.

**Call relations**: _translated calls this only for strings that start like data URLs and are under the decode limit. It uses _decoded_base64 for safe decoding and _translated_bytes for the final decision about where the decoded content should live.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 413–422)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the tool result. Small UTF-8 text stays inline so the model can read it directly; binary or large content is written to a workspace file.

**Data flow**: It receives decoded bytes, optional decoded text, a suggested name, and a MIME type. If the text exists and is below the inline size limit, it returns the text string. Otherwise it calls _offloaded to write the bytes into the workspace and returns that file reference.

**Call relations**: _translated_node and _translated_data_url both call this after decoding base64 content. It is the decision point between keeping useful small text in context and moving bulky content out to a workspace file.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 424–456)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded content into the workspace and returns a small reference object. This prevents large or binary payloads from bloating the tool result while still making the content available.

**Data flow**: It receives a filename, MIME type, and bytes. It cleans the filename, builds a content-addressed path using a SHA-256 hash of the bytes, writes to a temporary part file through the sandbox, then atomically renames it into place. It returns the name, workspace path, MIME type, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded content should not be inlined. It uses hashing, safe path handling, unique temporary names, and a sandbox move command so repeated identical payloads reuse the same final path without exposing readers to half-written files.

*Call graph*: called by 1 (_translated_bytes); 4 external calls (sha256, PurePosixPath, quote, uuid4).


##### `_ConnectorCall._deduped`  (lines 458–516)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the final connector result and, when safe and worthwhile, replaces repeated large objects with pointers to their first copy. This keeps repeated boilerplate from pushing useful results out of the model's immediate context.

**Data flow**: It receives the final payload dictionary. It first serializes it to JSON and checks size, structural complexity, and whether the provider already used the same pointer key. If any safety check fails, it returns the original JSON string. Otherwise it walks the payload, condenses repeated objects, and returns a new JSON string.

**Call relations**: _ConnectorCall.run calls this at the end inside asyncio.to_thread. It uses _escaped to build JSON Pointer paths and _condensed to do the recursive identity check and replacement.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 518–594)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Recursively examines one node of a JSON-like result and detects repeated object structures. It returns both the possibly rewritten node and a fingerprint used to compare it with later nodes.

**Data flow**: It receives a value, its JSON Pointer path, the current depth, and a table of first-seen object fingerprints. For dictionaries and lists it processes children and builds a SHA-256 digest from their structure. Large repeated dictionaries become a same_as pointer to the first location; small nodes, lists, and leaves are not replaced. It returns the transformed value, its digest, and its original-size estimate.

**Call relations**: _deduped calls this for each top-level item during result shrinking, and it calls itself for nested children. It uses _escaped when building child paths and hashing to compare structures without repeatedly serializing whole subtrees.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 597–600)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path segment for a JSON Pointer. A JSON Pointer is a standard string path used to point at a value inside a JSON document.

**Data flow**: It receives one object key as text. It replaces ~ and / with their special escaped forms so the key can safely appear inside a pointer path. It returns the escaped token.

**Call relations**: _deduped and _condensed use this when they create same_as pointers. It makes sure a key containing slash-like characters still points to the exact original object rather than being mistaken for path separators.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 603–627)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider claims is base64. It refuses oversized or invalid input instead of guessing, so ordinary strings are not accidentally corrupted.

**Data flow**: It receives any value. If it is not a string or is too long, it returns None. Otherwise it removes whitespace, strictly base64-decodes the string, then tries to decode the bytes as UTF-8 text. It returns the bytes plus text when text is valid, or bytes plus None for binary data; invalid base64 returns None.

**Call relations**: _translated_node uses this for fields marked by providers as base64, and _translated_data_url uses it for data URL payloads. It is the safety gate before decoded bytes are either inlined or offloaded.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 630–641)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs richer tool discovery within a single connector using a natural-language goal. It can return matching tool schemas plus broker guidance about how to use them.

**Data flow**: It receives a connector ID and query. It looks up the connector entry, asks the broker to search that connector's tools for the workspace, formats the returned tools, and includes plan, guidance, and pitfalls from the broker. It returns all of that as a JSON tool result.

**Call relations**: This public tool is useful when the agent knows the desired outcome but not the exact tool name. It gets the registry through _registry, formats broker tools with _tool_json, and wraps the response with _json_result.

*Call graph*: calls 3 internal fn (_json_result, _registry, _tool_json).


##### `_registry`  (lines 644–647)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Retrieves the connector registry for the current turn. The registry is the live map of which connector providers are available and which broker owns each one.

**Data flow**: It receives the tool context. If the context has no connector registry, it raises an error because connector tools cannot work without it. Otherwise it returns the registry object.

**Call relations**: All four public connector tool handlers call this before doing connector work. It centralizes the required context check so list, describe, search, and call all fail clearly if dispatched in the wrong environment.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 650–651)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the simple JSON shape returned to the agent. It keeps the public fields the agent needs: slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads the tool's slug, description, and input schema and places them into a plain dictionary. That dictionary can then be serialized into a tool result.

**Call relations**: describe_external_tools uses this when returning exact schemas, and search_connector_tools uses it for semantic search results. It keeps those public outputs consistent.

*Call graph*: called by 2 (describe_external_tools, search_connector_tools).


##### `_discovery_query`  (lines 654–661)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Builds a search query for tool discovery when the caller did not provide one. It turns unresolved guessed tool names into useful keywords.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is non-empty, it returns that unchanged. Otherwise it lowercases unresolved names, replaces non-letter and non-number characters with spaces, removes repeated words while preserving order, and returns the keyword string.

**Call relations**: describe_external_tools calls this when it needs to ask the broker for available tools after exact names did not resolve, or when no exact names were supplied. It uses regular-expression cleanup to turn slugs into human-searchable words.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 664–665)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a plain dictionary payload into the ToolResult format expected by the tool system. It is a small helper for returning JSON text consistently.

**Data flow**: It receives a dictionary. It serializes the dictionary with json.dumps, wraps the text in a TextContent object, then wraps that in a ToolResult. The returned object is ready for the tool framework to send back.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools all call this for their simple JSON responses. call_external_tool does not use it because _ConnectorCall.run already returns a serialized and possibly condensed JSON string.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation connector discovery and tool-call handling`

This file gives automated evaluations a controlled world for an agent to act in. Instead of mocking tool calls, it registers real connector providers for email, calendar, and code search. The agent discovers tools, reads their schemas, and calls them through the normal broker route. The difference is that the data lives in this extension’s own evaluation storage, so a test can seed an inbox or calendar, let the agent work, then check exactly what changed.

The email and calendar parts have database tables because the agent can mutate them. Sending an email inserts a row into a sent-mail table. Creating, updating, or cancelling a calendar event changes an event table. Everything is scoped by workspace ID, so one evaluation case does not leak into another. The code-search part is read-only: tests seed a full response under a query string, and the broker returns those exact bytes. That matters because evaluations may care about response size and formatting, not just the idea of a search result.

The file also declares the tool catalog: what tools exist, what arguments they accept, and how they are described to the agent. Finally, it builds the extension manifest, which is the registration document that tells the host system these three eval connectors exist.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Opens a database transaction tied to this extension’s workspace-scoped storage. A transaction is a safe unit of database work: either the whole change is saved, or none of it is.

**Data flow**: It takes no direct input. It creates an extension context for the eval environment, with no declared credentials, then returns the transaction object used to read or write the extension’s tables.

**Call relations**: The email and calendar operations call this whenever they need durable storage. Sending email, listing email, creating events, listing events, and changing events all use it so they work against the same scoped store that graders later inspect.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a Python datetime value. If the string has no timezone, it treats it as UTC so event times are not left ambiguous.

**Data flow**: It receives a text timestamp, parses it, checks whether timezone information is present, and returns a timezone-aware datetime. The input is plain text; the output is a time object suitable for database storage.

**Call relations**: Calendar creation and event updates use this before saving start or end times. It keeps the rest of the calendar code from repeating date parsing rules.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the available tools for one eval provider, such as email, calendar, or code search. If a search phrase is supplied, it narrows the list to tools whose name or description matches.

**Data flow**: It receives a workspace ID, provider name, and search query. It reads the in-file tool catalog, filters it when the query is not empty, and returns matching tool descriptions; if nothing matches, it returns the whole provider catalog.

**Call relations**: The broker search method calls this when the host asks what tools are available. It is part of the discovery path before an agent chooses a tool to call.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the exact schema for a named tool. A schema describes the shape of the arguments the agent must provide, like required fields and allowed values.

**Data flow**: It receives a workspace ID, provider name, and tool slug. It scans that provider’s catalog and returns the matching tool definition. If the slug is not known, it raises an UnknownBrokerTool error.

**Call relations**: This supports the normal connector flow where a client asks for details about one tool before calling it. It does not call helper methods inside this file, but it relies on the shared catalog declared near the top.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Routes an actual tool call to the correct email, calendar, or code-search action. It is the main dispatch point for the eval broker.

**Data flow**: It receives the workspace, provider, tool slug, raw argument values, account ID, and optional idempotency key. It validates the raw arguments with the right argument model, calls the matching private method, and returns that method’s result. If the provider or slug is unknown, it raises UnknownBrokerTool.

**Call relations**: The production connector machinery calls this when the agent invokes a tool. This method then hands off to _send_email, _list_emails, _create_event, _list_events, _update_event, _cancel_event, or _search_code depending on the requested tool.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the pre-seeded code-search response for a query. It deliberately fails if the test did not seed that query, because silently returning an empty result could hide a broken evaluation setup.

**Data flow**: It receives validated search arguments containing a query string. It looks up a stored value under a key based on that query, checks that the value is a dictionary, copies it, and returns it as the tool response.

**Call relations**: Execute calls this for the code-search provider’s search_code tool. It uses the scoped store directly because code-search fixtures are stored as exact response payloads rather than rows in a table.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Records a sent email in the eval mailbox. This simulates sending mail while keeping a durable row that a grader can later check.

**Data flow**: It receives the workspace ID and validated email arguments: recipients, subject, and body. It creates a new email ID, writes a sent-mail row with the fixed assistant sender address and current time, and returns the new ID, sent status, and recipients.

**Call relations**: Execute calls this when the agent invokes send_email. It uses _transaction to save the row in the extension’s storage.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the eval mailbox, optionally filtering by a text query. It gives the agent a realistic inbox or sent-folder listing.

**Data flow**: It receives the workspace ID and validated listing arguments: folder, query, and limit. It builds database conditions for the workspace and folder, adds a case-insensitive match over sender, subject, and body when a query is present, reads newest messages first, and returns them as simple dictionaries.

**Call relations**: Execute calls this for list_emails. It uses _transaction for the database read and SQLAlchemy query helpers to express the search.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Adds a confirmed event to the eval calendar. This lets an agent make calendar changes that persist for grading.

**Data flow**: It receives the workspace ID and validated event details: title, start, end, and attendees. It creates a new event ID, parses the start and end strings into times, inserts a confirmed event row, and returns the new ID and confirmed status.

**Call relations**: Execute calls this for create_event. It uses _moment to normalize time strings and _transaction to write the event row.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Returns calendar events for a workspace, ordered by start time. It can optionally filter by a phrase in the event title.

**Data flow**: It receives the workspace ID and validated listing arguments: query and limit. It selects matching event rows from the database, sorts them by start time, limits the count, converts each row into response JSON, and returns the list.

**Call relations**: Execute calls this for list_events. It uses _transaction for the database read and _event_json to format each event consistently.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares changes for an existing calendar event, such as a new title, time, or attendee list. It refuses an update request that does not actually change anything.

**Data flow**: It receives the workspace ID and validated update arguments. It builds a changes dictionary only from fields that were supplied, parses new time strings when present, and passes the event ID plus changes onward. The result is the updated event as a dictionary.

**Call relations**: Execute calls this for update_event. After collecting and normalizing the requested edits, it delegates the actual database update to _change_event.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled. The event is not deleted; it remains visible with a cancelled status, which mirrors many real calendar systems.

**Data flow**: It receives the workspace ID and a validated event ID. It builds a small change saying the status should become cancelled, then returns the updated event data.

**Call relations**: Execute calls this for cancel_event. It delegates the shared update-and-fetch work to _change_event.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the updated version. It is the shared database update path for both editing and cancelling events.

**Data flow**: It receives a workspace ID, event ID string, and a dictionary of fields to change. It converts the event ID to a UUID, updates only the row in that workspace, checks that exactly one row changed, reads the updated row back, and returns it in response format. If no matching event exists, it raises an error.

**Call relations**: _update_event and _cancel_event call this after deciding what should change. It uses _transaction for the database work and _event_json to shape the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Turns a database event row into the plain dictionary returned by calendar tools. This keeps event responses consistent across listing, updating, and cancelling.

**Data flow**: It receives one database row. It extracts the ID, title, start and end times, attendees, and status, converts the ID and times into strings, and returns a JSON-friendly dictionary.

**Call relations**: _list_events calls this for every listed event, and _change_event calls it after updating one event. It is the small formatter used at the end of calendar read and write flows.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that eval environment tool calls do not produce downloadable files. The broker interface asks for this hook, but these tools return only ordinary structured data.

**Data flow**: It receives a tool response dictionary and ignores it. It always returns an empty tuple, meaning there are no file attachments or generated files to expose.

**Call relations**: This fits the broader broker interface. Nothing in this file calls it directly, but the host can ask a broker whether a response includes files.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for the eval providers. Email, calendar, and code search in this environment are text-and-record tools only.

**Data flow**: It receives upload details such as workspace, provider, tool slug, filename, MIME type, and checksum. Instead of creating an upload target, it raises a runtime error saying uploads are not accepted.

**Call relations**: This exists because the broker interface includes upload staging. If the host ever tries to stage a file upload for these eval tools, this method stops it immediately.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery results in the broker search response type. It lets the host search for relevant tools in a provider.

**Data flow**: It receives a workspace ID, provider name, and query. It asks tools for the matching tool catalog, places that catalog inside a BrokerSearch object, and returns it.

**Call relations**: The host’s connector discovery flow can call this before an agent chooses a tool. Internally it delegates the actual matching to EvalEnvBroker.tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple bearer credential for the eval account. A bearer credential is a token-like value used to prove access, but here it is deterministic and local to evaluation.

**Data flow**: It receives the workspace ID, provider name, and account name. It builds and returns a Credential whose bearer value is prefixed with eval-env and includes the account.

**Call relations**: This satisfies the broker interface when the connector layer needs credentials. It does not contact an outside service; it creates the credential directly.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a pretend OAuth authorization URL for an eval provider. OAuth is the common web flow where a user grants an app access, but evaluations normally seed access directly instead.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider’s fake host into an authorization URL string and returns it.

**Call relations**: The manifest registers _EvalEnvOAuth objects because connector providers require an OAuth descriptor. This method would be used if someone drove the connect flow, though normal evals do not rely on it.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the pretend OAuth flow by returning the fixed eval account. It keeps the interface honest without involving a real authorization server.

**Data flow**: It receives a code, redirect URI, workspace ID, and state. It ignores the real meaning of those values and returns an OAuthAccount with the constant eval account ID.

**Call relations**: The connector framework may call this after an authorization redirect. In normal evaluations, grants are seeded directly, but this method is present because the provider registration expects an exchange step.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest that registers the eval email, calendar, and code-search connectors. A manifest is the host system’s map of what this extension offers.

**Data flow**: It creates one EvalEnvBroker, then builds three connector provider entries with labels, fake OAuth descriptors, and the shared broker. It returns a Manifest containing the extension name, version, and providers.

**Call relations**: The extension loader calls this to discover the extension. It wires together _EvalEnvOAuth, ConnectorProvider, EvalEnvBroker, and Manifest so the rest of the system can list and call the eval tools.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/mcp/ufo_ext_mcp.py`

`domain_logic` · `extension load and tool request handling`

This extension is a bridge between UFO and external MCP servers. Instead of hard-coding every possible outside tool, it gives the agent two general tools: one to ask a named MCP server what tools it offers, and one to call a specific tool after it has been discovered. This matters because each workspace can connect different servers, with different tool names and argument shapes.

The file reads server settings from a protected credential slot called `mcp_servers`. That setting is expected to name servers and give each one a URL and optional bearer token, which is a common HTTP authentication token. It validates that server URLs are plain HTTP or HTTPS before making calls.

When listing tools, the extension connects to the chosen server through FastMCP’s HTTP client, asks for the server’s tool catalog, and returns each tool’s name, description, input schema, and whether it looks safe to repeat. When calling a tool, it first checks that the request is not too large, sends the call, then turns the answer into a normal UFO tool result. Results are also size-checked.

A key safety point is that MCP servers are external. Their output may be attacker-controlled, so the manifest marks these tools as untrusted and the descriptions warn the model not to treat the content as inherently safe.

#### Function details

##### `McpServer._http_url`  (lines 70–73)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates that an MCP server address starts with `http://` or `https://`. It prevents the extension from accidentally accepting some other kind of address that this transport is not meant to use.

**Data flow**: A URL string comes in while an `McpServer` configuration object is being built. The function checks it against the allowed HTTP/HTTPS pattern. If it matches, the same URL goes forward; if not, validation stops with an error.

**Call relations**: This is used automatically by Pydantic, the data validation library, when workspace MCP server credentials are parsed. It is an early gate before `_server` can return a server for `_list_mcp_tools` or `_call_mcp_tool` to contact.


##### `mcp_client`  (lines 103–109)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This builds the HTTP client used to talk to one MCP server. It also attaches the server’s bearer token, if one was configured, so the server can authenticate the request.

**Data flow**: An `McpServer` object comes in with a URL and optional auth token. The function turns the token into an `authorization` header when present, creates a Streamable HTTP transport for the URL, and returns a FastMCP client with a fixed timeout.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` has found the right configured server. The returned FastMCP client takes care of MCP protocol details such as initialization and HTTP streaming, so the rest of this file can simply ask to list tools or call one.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 112–124)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up a named MCP server from the workspace’s protected `mcp_servers` credential. It makes sure the extension never blindly calls an unknown or missing server.

**Data flow**: The tool context and requested server name come in. The function reads the credential value through the extension context, parses and validates it as MCP server configuration, then searches for the requested name. It returns the matching `McpServer`, or raises a clear error if the context, credential, or name is not valid.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` start by calling this. It is the shared doorway from a user-facing tool request into the workspace’s private server configuration.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 127–143)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This implements the user-visible `list_mcp_tools` tool. It asks a configured MCP server what tools it offers so the agent can choose exact tool names and argument formats instead of guessing.

**Data flow**: The tool context and listing request come in, including the server name. The function resolves that server with `_server`, opens an MCP client with `mcp_client`, asks the server for its tool list, and reshapes the answer into a JSON result containing names, descriptions, input schemas, and idempotence hints. The final output is a `ToolResult` made by `_json_result`.

**Call relations**: This is one of the handlers registered by `manifest`. In the normal flow, the agent should call this before `_call_mcp_tool`; the result tells the agent which remote tools exist and what arguments they expect.

*Call graph*: calls 3 internal fn (_json_result, _server, mcp_client).


##### `_call_mcp_tool`  (lines 146–158)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This implements the user-visible `call_mcp_tool` tool. It sends a chosen tool name and JSON arguments to a configured MCP server, then converts the server’s answer into UFO’s standard tool-result format.

**Data flow**: The tool context and call request come in, including server name, tool name, and arguments. The function finds the server, serializes the arguments to check they are no larger than one mebibyte, opens an MCP client, and calls the remote tool. If the remote server reports an error, it returns an error `ToolResult` with bounded text. If the server returns structured JSON content, that is returned as JSON; otherwise text blocks are joined and returned as JSON under a `text` field.

**Call relations**: This is the second handler registered by `manifest`, and it is meant to be used after `_list_mcp_tools` has identified the exact remote tool and schema. It relies on `_server` for configuration lookup, `mcp_client` for the connection, `_joined_text` for plain-text MCP responses, `_bounded` for size safety, and `_json_result` for normal JSON-shaped replies.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 161–162)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This extracts readable text from an MCP response that may contain several content blocks. It ignores non-text blocks and joins the text blocks with line breaks.

**Data flow**: A list of MCP content objects comes in. The function keeps only objects that are MCP text content, takes their `.text` values, joins them with newline characters, and returns one string.

**Call relations**: `_call_mcp_tool` uses this when a remote tool does not return structured JSON or when it reports an error. It is the small adapter that turns MCP’s block-style response into a simple string for UFO’s tool result.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 165–168)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for text returned from MCP-related work. It fails loudly instead of silently cutting off data, which avoids misleading the model with partial results.

**Data flow**: A text string comes in. The function measures its encoded byte length and compares it with the one-mebibyte response limit. If it is small enough, the same text comes out; if it is too large, an `McpError` is raised.

**Call relations**: `_json_result` uses this before packaging JSON output, and `_call_mcp_tool` uses it for remote error text. It is the shared safety check at the point where external MCP content is about to become a UFO tool result.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_json_result`  (lines 171–172)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This wraps a Python dictionary as a UFO tool result containing JSON text. It gives the two MCP tools a consistent way to return machine-readable answers.

**Data flow**: A dictionary payload comes in. The function serializes it to a JSON string, passes that string through `_bounded` to enforce the response size limit, puts it into a `TextContent` object, and returns a `ToolResult` containing that text.

**Call relations**: `_list_mcp_tools` uses this to return discovered tool catalogs, and `_call_mcp_tool` uses it for successful remote responses. It is the final packaging step for normal, non-error results.

*Call graph*: calls 1 internal fn (_bounded); called by 2 (_call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 175–206)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to UFO: its name, version, public tools, input models, handlers, trust boundary, and required credential slot. Without this manifest, the platform would not know how to expose the MCP listing and calling tools.

**Data flow**: No runtime request data comes in. The function builds and returns a `Manifest` object that names the extension, registers `list_mcp_tools` and `call_mcp_tool`, connects each to its input model and handler function, marks both as untrusted, and declares the `mcp_servers` credential slot.

**Call relations**: The host system calls this when loading the extension. The manifest is the wiring diagram: later, when the agent invokes one of the registered tools, UFO dispatches to `_list_mcp_tools` or `_call_mcp_tool` using the definitions created here.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Search and research tools
Search provider and research tool files route web, page-fetch, and category-search requests through safe external search integrations.

### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `request handling`

This file is an adapter: it translates the project’s own search requests into Exa API calls, then translates Exa’s answers back into the project’s standard search result shapes. Without it, choosing the Exa search provider in configuration would not work, and research tools would have no way to ask Exa for search results or page text.

The main class, ExaSearchProvider, runs on the host side of the serve process. That matters because it reads the user’s “bring your own key” Exa API key directly through CredentialAccess, instead of passing the key into a sandbox where tools run. Think of it like a receptionist making the paid phone call on behalf of someone in a locked room: the room can request the call, but never sees the credit card number.

For search, the provider builds an Exa /search request from a SearchQuery. It adds details such as result count, allowed domains, recency limits, or special vertical search categories like academic papers. For fetching, it calls Exa’s /contents endpoint to read text from one URL, optionally asking Exa for a summary or forcing a fresh crawl.

The file also checks that Exa’s response really contains a results list. Bad HTTP responses or malformed bodies raise ExaError instead of pretending there are no results, which makes failures visible and easier to diagnose.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs one web search through Exa and returns the answer in the project’s standard SearchResults format. A caller uses this when it has a SearchQuery and wants a list of matching pages.

**Data flow**: It receives a SearchQuery containing the search text and options such as number of results, recency, domains, or vertical type. It turns that query into an Exa request body, sends it to Exa’s search endpoint, checks the returned results list, converts each raw Exa item into a SearchHit, and returns a SearchResults object containing those hits.

**Call relations**: This is the main search entry used by the project’s search-provider seam. During the search flow it asks _search_body to shape the request, _post to send it over HTTP with credentials, _results to validate and extract Exa’s result list, and _hit to translate each item into the project’s own result type.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable content for a single URL through Exa. A caller uses this after finding or knowing a page URL and wanting page text, and possibly a summary.

**Data flow**: It receives a FetchRequest with a URL, optional maximum text length, optional summary prompt, and an optional force-refresh flag. It builds an Exa contents request, caps the requested text size to the file’s maximum, sends the request, extracts the first result if one exists, and returns a FetchedPage with the URL, text, and optional summary.

**Call relations**: This is the fetch side of the same provider seam as search. It relies on _post to contact Exa, _results to make sure Exa returned a usable results list, and _opt_str to keep optional summary data only when Exa actually returned a string.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–91)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON body that Exa expects for a search request. It hides Exa-specific request details from the higher-level search method.

**Data flow**: It receives a SearchQuery. For normal web search, it asks Exa for text snippets and highlights, and may add allowed domains or a starting publication date based on recency. For vertical searches, it asks for shorter text and may map the project’s vertical name to Exa’s category name. It returns a dictionary ready to send as JSON.

**Call relations**: ExaSearchProvider.search calls this before making the network request. When a recency option is present, it uses the current UTC time and subtracts a fixed number of days so Exa only searches newer material.

*Call graph*: called by 1 (search); 2 external calls (now, timedelta).


##### `ExaSearchProvider._hit`  (lines 94–104)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Turns one raw Exa search result into the project’s standard SearchHit object. This keeps the rest of the system from needing to know Exa’s field names.

**Data flow**: It receives one dictionary from Exa. It reads fields such as URL, title, text, publication date, and highlights; replaces missing main fields with empty strings; keeps the publication date only if it is a string; and filters highlights so only text values remain. It returns a SearchHit.

**Call relations**: ExaSearchProvider.search calls this once for each result returned by _results. It uses _opt_str for safe optional string conversion and then hands the cleaned data into the SearchHit model.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 106–114)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends an authenticated HTTP POST request to Exa and returns the decoded JSON response. It is the one place in this provider that actually talks to the Exa API.

**Data flow**: It receives an API path, such as /search or /contents, and a JSON-ready request body. It reads the Exa API key from credentials, opens an async HTTP client pointed at api.exa.ai, sends the request with the key in the x-api-key header, and returns the response JSON. If Exa reports an HTTP error, it raises ExaError with the status and response text.

**Call relations**: Both search and fetch call this whenever they need to contact Exa. Tests can provide a custom HTTP transport through the provider object, while production leaves that unset so normal network transport is used.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 117–121)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Checks an Exa response and extracts its results list. It prevents malformed responses from being mistaken for a legitimate empty answer.

**Data flow**: It receives the decoded response payload from Exa. If the payload is a dictionary with a results field that is a list, it keeps only list items that are dictionaries and returns them. If there is no valid results list, it raises ExaError.

**Call relations**: Both ExaSearchProvider.search and ExaSearchProvider.fetch call this after _post returns. It acts as a shared safety gate before those methods convert the data into SearchHit or FetchedPage objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 124–125)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only when it is actually a string. This is a small guard against unexpected response shapes from Exa.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns None. It does not change anything else.

**Call relations**: ExaSearchProvider._hit uses it for optional publication dates, and ExaSearchProvider.fetch uses it for optional summaries. In both places, it keeps non-text values from leaking into fields that should be text or empty.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 128–141)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system so it can be discovered and selected. It declares the Exa credential slot and says how to build an ExaSearchProvider.

**Data flow**: It takes no input. It creates a Manifest containing the extension name, version, one credential slot for the Exa API key, and one search provider specification whose builder constructs ExaSearchProvider with the provided credentials. It returns that Manifest to the extension loader.

**Call relations**: The host calls this when loading the extension. The returned SearchProviderSpec is what lets configuration choose the Exa backend, and the CredentialSlot tells the host which secret must be available before the provider can make requests.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `tool invocation during request handling`

This file is the bridge between an agent asking for online information and the search service that can provide it. Without it, the agent would not have a standard way to say “search these terms,” “read this URL,” or “look for videos/products/people,” and different search backends would be harder to use consistently.

The file defines three tools: `search_web`, `fetch_url`, and `search_vertical`. Each tool has an input model that describes what arguments are allowed. These models are built with Pydantic, a library that checks and shapes incoming data, so bad requests are caught early.

When a tool runs, it first asks the current tool context for a `SearchProvider`, meaning the search backend chosen for this turn. If none exists, the code fails loudly instead of pretending search worked. Web search can accept several short queries, run them one by one, merge the results, and return them as JSON. Vertical search is similar, but adds a category such as image, academic, video, people, or shopping. Fetching a URL is treated more carefully: it only works if the provider says it supports fetching, and every fetched page includes a clear warning that the content came through the provider’s crawler session, not the user’s workspace or account. This matters because a fetched page may reflect the crawler’s identity, not the user’s.

#### Function details

##### `_provider`  (lines 120–123)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function retrieves the search backend for the current tool run. It exists so all research tools share the same check: if no search provider was configured, stop immediately with a clear error.

**Data flow**: It receives a `ToolContext`, which is the object holding information about this tool call. It reads `ctx.search_provider`; if that value is missing, it raises an error. If it is present, it returns the provider so the calling tool can search or fetch.

**Call relations**: The web search, URL fetch, and vertical search functions all call this first. It acts like a front desk: before any tool can ask the outside search service for information, `_provider` confirms that there is actually a service available.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 126–140)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search results into a simple JSON string for the agent to read. It keeps the output shape consistent across normal web searches and vertical searches.

**Data flow**: It takes a list of search hits and an optional direct answer. For each hit, it copies the useful fields: URL, title, snippet text, published date, and highlights. It puts those into a dictionary, adds the answer if one exists, and converts the whole package into JSON text.

**Call relations**: Both `_search_web` and `_search_vertical` call this after they receive results from the search provider. It is the final packaging step before the result is wrapped as tool output.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 143–158)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the implementation behind the `search_web` tool. It searches the general web for one or more short queries and returns a combined set of results.

**Data flow**: It receives the tool context and validated `SearchWebInput`, including queries, optional recency limits, optional allowed domains, and a user-facing description. It gets the configured provider, sends each query to that provider with a default number of results, collects all returned hits, keeps the first direct answer if one is provided, converts everything to JSON, and returns it as text inside a `ToolResult`.

**Call relations**: When the `search_web` tool is invoked, this function does the work. It first calls `_provider` to get the backend, then creates a `SearchQuery` for each requested search, waits for the provider’s response, and finally calls `_results_json` so the output matches the common search-result format.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


##### `_fetch_url`  (lines 161–180)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the implementation behind the `fetch_url` tool. It asks the search provider to retrieve the contents of a public web page and returns the page text, plus an important warning about where that fetch came from.

**Data flow**: It receives the tool context and validated `FetchUrlInput`, including the URL, optional extraction prompt, optional maximum length, and cache-bypass flag. It gets the provider and first checks whether that provider can fetch pages. If fetching is unsupported, it returns an error message. If supported, it builds a fetch request, waits for the provider to retrieve the page, then returns JSON containing the final URL, page text, crawler provenance warning, and summary if one was supplied.

**Call relations**: When the `fetch_url` tool is invoked, this function coordinates the fetch. It calls `_provider` for the backend, hands a `FetchRequest` to that backend, and wraps the backend’s page response in a `ToolResult`. Unlike the search functions, it does its own JSON packaging because fetched pages have different fields from search hits.

*Call graph*: calls 1 internal fn (_provider); 4 external calls (__init__, __init__, __init__, dumps).


##### `_search_vertical`  (lines 183–190)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the implementation behind the `search_vertical` tool. It searches a specialized kind of content, such as images, videos, academic papers, professional profiles, or shopping results.

**Data flow**: It receives the tool context and validated `SearchVerticalInput`, including the vertical category and search query. It gets the configured provider, sends one search request with the category attached, converts the provider’s hits and optional answer into JSON, and returns that text as a `ToolResult`.

**Call relations**: When the `search_vertical` tool is invoked, this function runs the category-specific search. It follows the same pattern as `_search_web`: get the provider through `_provider`, ask the provider to search using `SearchQuery`, then hand the results to `_results_json` for consistent output.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


### Interactive work tools
Interactive extensions support persistent code execution, Slack setup and discovery, durable task tracking, and YC command-line access.

### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `tool registration and tool request handling`

This file gives the system two interactive workbenches inside the sandbox: one for Node.js JavaScript, and one for Python spreadsheet work with openpyxl. A REPL is a place to run small pieces of code step by step; here it is “persistent,” meaning earlier successful code is saved and replayed before the next snippet. This is like keeping a notebook where only the pages that worked are copied into the next session, so a failed experiment does not poison future work.

For each run, the file builds a temporary combined script from the saved history plus the new code. If the user asks for a reset, it deletes the saved history first. The script then runs inside the sandbox, so the normal workspace, network, and isolation rules still apply. If the script exits successfully, the combined code becomes the new saved state. If it fails, the saved state is left unchanged.

The JavaScript tool adds a small helper called emitImage, so browser automation or visualization code can return images inline. It also creates links to globally installed Node packages so imports such as Playwright can work from the temporary run file. The Python spreadsheet tool appends a footer that prints the variable result as JSON when it exists. Finally, manifest() publishes these tools and the related data skills to the host system.

#### Function details

##### `global_modules_link`  (lines 54–72)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because ES modules do not automatically search NODE_PATH, so bare imports like Playwright may fail unless the packages are linked into the REPL’s local node_modules folder.

**Data flow**: It takes a tuple of possible global module locations. It turns them into one shell command that creates the REPL node_modules directory, removes an old whole-directory symlink if needed, and adds per-package symlinks for packages it finds. The output is a command string; it does not run the command itself.

**Call relations**: js_repl calls this before running Node.js code. If the linking command fails, js_repl stops and reports an error instead of running code with broken imports.

*Call graph*: called by 1 (js_repl).


##### `_candidate_source`  (lines 162–168)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be tried for the next REPL run. It combines the saved successful history with the new snippet, or starts fresh when reset is requested.

**Data flow**: It receives the sandbox context, the path to the saved REPL state file, the new code, and whether to reset. If reset is true, it deletes the old state file. If there is no saved state, it returns just the new code plus a newline. Otherwise, it reads the existing saved code and appends the new code. The returned text is a candidate script; it is not saved permanently yet.

**Call relations**: Both js_repl and xlsx_repl call this at the start of a tool run. They only write this candidate back to the persistent state file after the sandboxed execution succeeds.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 171–182)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Packages the result of a REPL run into the standard tool response format. It records printed output, error output, the process exit code, and optionally images.

**Data flow**: It receives stdout, stderr, an exit code, and any image objects. It creates a text item containing a JSON summary of stdout, stderr, and exit_code, then appends the images after that. It returns a ToolResult and marks it as an error whenever the exit code is not zero.

**Call relations**: js_repl and xlsx_repl call this after their sandbox command finishes. It is the final wrapper that turns raw process output into something the rest of the UFO tool system can display and reason about.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_emitted_images`  (lines 190–201)

```
async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code sent through emitImage and converts them into tool response image objects. It quietly ignores malformed image records instead of failing the whole tool call.

**Data flow**: It receives the sandbox context and checks for the JSON-lines file where emitImage writes image data. If the file is absent, it returns no images. If present, it reads recent non-empty lines, validates each as an emitted image with a media type and base64 data, and returns a tuple of ImageContent objects.

**Call relations**: js_repl calls this after Node.js finishes. The images it returns are handed to _repl_result, which includes them alongside the text output.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 204–216)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs JavaScript code in a persistent Node.js REPL inside the sandbox. It is meant for browser automation, website testing, game testing, visualization, and other JavaScript-heavy exploration where earlier variables and imports should stay available.

**Data flow**: It receives the tool context and validated JavaScript input. It builds a candidate script from saved state plus new code, writes a temporary .mjs run file with the emitImage prelude at the top, clears any old emitted-image file, links global Node packages, and runs Node with a timeout. If Node exits successfully, it saves the candidate script as the new persistent state. It returns stdout, stderr, exit code, and any emitted images in a ToolResult.

**Call relations**: This is the handler registered for the js_repl tool by manifest(). During a tool call, it leans on _candidate_source to prepare code, global_modules_link to make imports work, _emitted_images to collect visual output, and _repl_result to format the final response.

*Call graph*: calls 4 internal fn (_candidate_source, _emitted_images, _repl_result, global_modules_link); 1 external calls (quote).


##### `xlsx_repl`  (lines 219–227)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs Python code in a persistent spreadsheet-focused REPL inside the sandbox. It is designed for Excel workbook inspection and editing with openpyxl, while preserving successful imports, variables, and loaded workbooks between calls.

**Data flow**: It receives the tool context and validated Python input. It builds a candidate Python script from saved state plus new code, writes a temporary run file, and appends a footer that prints result as JSON if that variable exists. It runs python3 with a timeout. If execution succeeds, it saves the candidate script as the new persistent state. It returns stdout, stderr, and exit code as a ToolResult.

**Call relations**: This is the handler registered for the xlsx_repl tool by manifest(). It uses _candidate_source before execution and _repl_result afterward; unlike js_repl, it does not collect images or link Node modules.

*Call graph*: calls 2 internal fn (_candidate_source, _repl_result); 1 external calls (quote).


##### `manifest`  (lines 230–250)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, available tools, input schemas, handlers, related skills, and sandbox internet setting. Without this, the REPL tools and skills would not be discoverable.

**Data flow**: It takes no input. It builds ToolDef entries for the JavaScript and spreadsheet REPLs, points them at their input models and handler functions, creates SkillSpec entries for the bundled skill folders, and returns a Manifest object with sandbox internet enabled.

**Call relations**: The extension loader calls this when registering the pack. The returned Manifest is how the host learns to route js_repl calls to js_repl, xlsx_repl calls to xlsx_repl, and expose the listed data skills.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup, connection checks, and Slack conversation lookup during tool use`

This file is the Slack “setup desk” for UFO. It lets an admin connect a Slack workspace either with a one-click Slack OAuth install, or by creating their own Slack app from a ready-made manifest. OAuth is the familiar “Add to Slack” button path. The manifest path is for teams that want to bring their own Slack app and privately provide the bot token and signing secret. Without this file, the agent would not know how to guide Slack installation, check whether the connection is ready, or search Slack channels by human-friendly names. The main setup tool, slack_connect, behaves like a checklist. First it looks for stored Slack credentials. Then it tries to prove the bot’s identity. Then it records which Slack team belongs to this UFO workspace. Finally it checks whether Slack has actually reached this server with a valid signed request. That last check matters because having a token is not enough; Slack must also be able to call the deploy’s public URL. The file also provides slack_app_manifest, which prints the exact Slack app configuration to paste into Slack, and slack_channels, which uses the bot token to list or search channels, group chats, and direct messages. Search results are marked untrusted because names, topics, and purposes come from Slack users, not from trusted system code.

#### Function details

##### `_events_url`  (lines 139–140)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to this UFO deploy. It turns the deploy’s base URL into the specific Slack endpoint.

**Data flow**: It receives a public base URL, removes any trailing slash, adds /surface/slack, and returns the finished event URL string.

**Call relations**: The Slack connection flow uses this when reporting where Slack should send requests, and the manifest generator uses it when filling in the Slack app manifest. It is a small shared helper so both setup paths point Slack at the same endpoint.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 143–145)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a Slack setup status into the standard tool response format. It gives the agent a machine-readable JSON message with a state, a human hint, the Slack events URL, and any extra details.

**Data flow**: It receives a state name, a plain-language hint, an optional events URL, and extra fields. It builds a dictionary, converts it to JSON text, wraps that text in tool content, and returns a tool result.

**Call relations**: The main setup flow and its two install helpers call this whenever they need to explain the current Slack setup state. It is the common exit door for statuses such as not_configured, not_installed, pending, and connected.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 148–193)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the Slack connection checklist from start to finish. Someone can call it repeatedly before, during, or after setup, and it reports the current state instead of blindly starting over.

**Data flow**: It reads the workspace’s stored Slack bot token if one exists, tries to read or derive the Slack identity, and then binds that Slack team to the current UFO workspace. If identity is missing, it either creates an OAuth install link or walks the manifest-based setup path. At the end, it checks whether Slack has successfully contacted this deploy and returns a JSON status such as not_configured, not_installed, pending, or connected.

**Call relations**: This is the handler behind the slack_connect tool. It calls _events_url to name Slack’s callback address, _oauth_link for the one-click install path, _derive_manifest_identity for the bring-your-own-app path, _verified to see whether Slack has reached the server, and _state to return clear status messages.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 196–226)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the “Add to Slack” link for deployments that have their own Slack app configured. It is the one-click install path for admins.

**Data flow**: It checks whether the deployment has Slack client credentials in environment variables, confirms the speaker is a workspace admin, and requires a public base URL. If all checks pass, it starts a sealed credential authorization handoff and uses it to build a Slack authorization URL. It returns a status containing that URL, or a status explaining why OAuth cannot be used yet.

**Call relations**: slack_connect_handler calls this when no Slack identity exists and the requested install method is OAuth. This function hands off to the credential authorization system to protect the install payload, then uses Slack URL helpers to produce the link the admin opens.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 229–263)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-app setup path after the admin has privately provided Slack secrets. It proves which Slack team and bot the token belongs to.

**Data flow**: It checks whether both required secret slots are filled: the bot token and the signing secret. If anything is missing, it returns a not_configured status listing what still needs to be collected. If secrets exist, it reads the bot token, verifies the speaker is an admin, asks SlackIdentityResolver to confirm the bot’s Slack identity, and returns either that identity or a helpful error status.

**Call relations**: slack_connect_handler calls this when the user chose the manifest method and no identity is already stored. It uses _state for setup diagnoses and _token_diagnosis to turn Slack token errors into plain-language repair instructions.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 266–285)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has actually reached this deploy using the current signing secret. This is the difference between “we have credentials” and “Slack can really talk to us.”

**Data flow**: It looks in blob storage for a marker written after a valid Slack request. It reads and parses that marker, then reads the current Slack signing secret. It compares the marker’s fingerprint with the fingerprint of the current secret and returns true only if they match.

**Call relations**: slack_connect_handler calls this after identity is known. If it returns true, the setup is connected; if not, the setup is still pending because Slack has not yet made a verified request to this deploy, or the signing secret changed.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, signing_secret_fingerprint, url_verified_blob_key).


##### `slack_manifest_handler`  (lines 288–303)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for the current deploy. This helps a user create a Slack app with the correct permissions, event subscriptions, and request URLs without assembling them by hand.

**Data flow**: It receives the desired bot display name, checks that the name is simple and within Slack’s expected length, and requires the deploy to have a public base URL. It builds the Slack events URL, fills the manifest template with the bot name and request URLs, and returns the manifest as text.

**Call relations**: This is the handler behind the slack_app_manifest tool. The tool framework calls it when the agent needs to guide the manifest setup path, and it uses _events_url so the generated manifest points Slack at the same endpoint used by the connection checker.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 306–329)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations by name, topic, purpose, or people in a direct message. This lets the agent find a channel or DM from a human description instead of needing an exact Slack ID.

**Data flow**: It reads the stored Slack bot token, then reads the saved Slack identity for the current workspace. If either is missing, it raises a clear setup error. Otherwise it runs a Slack conversation search using the bot token, bot user ID, and user’s query, then returns JSON containing matching conversations and whether the scan was truncated.

**Call relations**: This is the handler behind the slack_channels tool. It relies on the identity created by slack_connect_handler and delegates the actual Slack paging and matching work to SlackConversationSearch. It marks its result as untrusted because Slack conversation names and descriptions are user-written content.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 332–338)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns a Slack authentication error code into a clearer message for the user. It especially recognizes cases where the bot token is missing, revoked, inactive, or invalid.

**Data flow**: It receives a Slack error string. If the error is one of the known token-rejection cases, it returns instructions to re-copy the Bot User OAuth Token and collect it again. Otherwise it returns a generic Slack auth.test failure message with the error included.

**Call relations**: _derive_manifest_identity calls this when Slack rejects the token during the manifest setup path. Its job is to make a low-level Slack error useful to the admin trying to fix setup.

*Call graph*: called by 1 (_derive_manifest_identity).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `tool invocation and extension registration`

This file gives the agent a visible progress board, like a checklist on a clipboard. Without it, the agent could still think through steps, but there would be no durable, shared todo list for the user interface to show, and no reliable way to update task status across turns in the same conversation.

The file defines the shapes of todo data: a task has text and a status, and a board has a title plus a list of tasks. It exposes two tools. The first, `update_todo_list`, creates or replaces the whole checklist. The second, `update_todo_status`, changes the status of one or more existing tasks, using 1-based task numbers so the first visible task is index 1.

The checklist is not stored only in the model's current memory. It is written into the extension's scoped store under a key based on the conversation ID. That means the same conversation can read back the board it previously saved. The file also packages these tools into a `manifest`, which is the extension's menu card: it tells the host system the extension name, version, available tools, input formats, and prompt text to include.

#### Function details

##### `_require_ext`  (lines 82–85)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call really has access to the todos extension context. The extension context is needed because it contains the durable store where the checklist is saved.

**Data flow**: It receives the current tool context. If the context includes an extension object, it returns that object. If not, it stops the call by raising an error, because the todo tools cannot safely read or write their board without extension storage.

**Call relations**: Both `update_todo_list` and `update_todo_status` call this at the start. It acts like checking that the clipboard exists before trying to write on it or read from it.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 88–89)

```
def _board_key(ctx: ToolContext) -> str
```

**Purpose**: This helper builds the storage key used for the todo board for the current conversation. It keeps each conversation's checklist separate from every other conversation's checklist.

**Data flow**: It reads the conversation ID from the tool context, adds the todo key prefix in front of it, and returns the resulting string. Nothing else is changed.

**Call relations**: `update_todo_list` uses this key when saving a new board, and `update_todo_status` uses the same key when finding and rewriting an existing board. This is what makes both tools point to the same saved checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_result`  (lines 92–93)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns a todo board into the standard tool response returned to the agent. It ensures every create or update call gives back the current full checklist.

**Data flow**: It receives a `TodoBoard`, converts it to JSON text, wraps that text in a `TextContent` object, then wraps that in a `ToolResult`. The output is the response the tool caller can read.

**Call relations**: After `update_todo_list` creates a board, and after `update_todo_status` changes one, both call this helper to return the latest board in the same format.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 96–98)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store and turns it back into a validated `TodoBoard` object. It is used when updating task statuses, because the tool must start from the current saved list.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value at that key. If nothing is saved, it returns `None`; otherwise it validates the stored data as a todo board and returns that board.

**Call relations**: `update_todo_status` calls this before applying status changes. It is the bridge from durable storage back into normal in-code todo data.

*Call graph*: called by 1 (update_todo_status).


##### `update_todo_list`  (lines 101–105)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates a new checklist or replaces the existing one for the conversation. It is meant to be called at the start of complex work so progress can be shown and updated.

**Data flow**: It receives the tool context and the requested list details: a title, tasks, and a user-facing description. It checks for the extension context, builds a `TodoBoard`, saves it in the store under the conversation-specific key, and returns the complete board as JSON text in a tool result.

**Call relations**: This is one of the two public tools registered by `manifest`. It relies on `_require_ext` to get storage access, `_board_key` to choose where to save the board, and `_board_result` to report the saved checklist back to the caller.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 108–119)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that marks existing checklist items as pending, in progress, or completed. It lets the agent update progress immediately as work starts and finishes.

**Data flow**: It receives the tool context and one or more status updates. It gets the extension context, builds the conversation-specific key, reads the saved board, and refuses to continue if no board exists. For each requested update, it checks that the 1-based task number is inside the list, changes that task's status, saves the revised board, and returns the full updated checklist.

**Call relations**: This public tool is registered by `manifest` alongside `update_todo_list`. It depends on `_read_board` to load the current checklist, then uses `_board_result` so the caller sees the new state after the changes.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `manifest`  (lines 122–141)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the todos extension to the host system. It tells the system what the extension is called, which tools it provides, what inputs those tools expect, and what prompt guidance should be included.

**Data flow**: It creates a `Manifest` object containing the extension name and version, two tool definitions, and one prompt section loaded from the nearby markdown file. The returned manifest is the package description the host uses to make the extension available.

**Call relations**: The host system calls this when registering or loading the extension. Through the returned tool definitions, it connects the tool names `update_todo_list` and `update_todo_status` to their actual handler functions.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/yc/ufo_ext_yc/cli.py`

`io_transport` · `request handling`

This file solves two related problems. First, UFO needs to run the external `yc` command-line program as if a signed-in user were running it. Second, UFO needs a way for a workspace admin to connect YC credentials in the first place. Without this file, YC tools would either have no login, leak credentials into the host environment, or run the CLI in an unsafe, hard-to-control way.

The auth side uses a “device authorization” flow, like signing into a TV app: UFO asks YC for a code and a web link, the admin opens the link and approves access, then UFO comes back later to exchange the approval for tokens. Pending sign-ins are stored in a sealed credential authorization record, so the sensitive device code is not just left in plain storage.

The CLI side takes saved YC credentials, creates a temporary private home folder, writes the credentials where the `yc` program expects them, runs the requested command, captures its output, and then deletes the temporary folder. It also notices if the CLI refreshed the token and carefully writes the new credential back, avoiding accidental overwrites when another run changed it first.

The read tool is a small translator: user-facing actions such as “search” or “skills_list” become exact `yc` command arguments.

#### Function details

##### `YcDeviceAuthorization.validate_verification_url`  (lines 59–63)

```
def validate_verification_url(self) -> 'YcDeviceAuthorization'
```

**Purpose**: This checks that YC’s login response points the user back to the real Y Combinator account website. It prevents a bad or unexpected response from sending an admin to a different host.

**Data flow**: It reads the verification link fields already parsed into the authorization object. It chooses the complete link if present, otherwise the base link, checks the host name, and either returns the same object as valid or raises an error.

**Call relations**: This validation happens when the device authorization response from YC is turned into a structured object during the start of sign-in. It acts as a guard before the link is shown back to the user.


##### `YcRunner.run`  (lines 94–94)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This is a protocol method, meaning it describes the shape of any object that can run YC commands. It lets `YcRead` depend on “something that can run commands” rather than one exact implementation.

**Data flow**: A runner receives command arguments and a session label, then returns the command’s text output. The protocol itself does not perform work; it states what callers can expect.

**Call relations**: YcRead calls this method when it has translated a user action into YC CLI arguments. In normal use, `YcCli` supplies the concrete implementation.


##### `YcAuth.run`  (lines 102–115)

```
async def run(self, action: Literal['start', 'complete'], session: str) -> YcAuthResult
```

**Purpose**: This is the public entry point for YC authorization work. It checks that the request is allowed and then sends the flow to either the start step or the completion step.

**Data flow**: It receives an action, either `start` or `complete`, plus a session label. It reads the tool context to confirm the extension exists, credential storage is available, the YC credential slot is declared, and the speaker is an admin. If all checks pass, it returns an auth result from the chosen step.

**Call relations**: The top-level `yc_auth` tool calls this function for every auth request. This function then calls `_start` when beginning a sign-in, or `_complete` when checking whether the user has finished approving access.

*Call graph*: calls 2 internal fn (_complete, _start).


##### `YcAuth._start`  (lines 117–175)

```
async def _start(self, session: str) -> YcAuthResult
```

**Purpose**: This begins the YC sign-in flow and returns the link and code the admin should use. It also avoids starting duplicate flows when the same request is retried.

**Data flow**: It reads any pending authorization from the extension store and checks the current credential digest, which is a fingerprint of stored credentials. If a matching pending flow already exists, it reopens it and returns the same login link and code. Otherwise it asks YC’s auth server for a new device code, seals the pending details through the credential system, stores a small reference record, and returns `authorization_required` with the link and user code.

**Call relations**: This is called by `YcAuth.run` for the `start` action. It uses `_credential_digest` to notice whether credentials changed, `_headers` to label the HTTP request, and `_bound` to reject oversized YC responses before parsing them.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, time).


##### `YcAuth._complete`  (lines 177–229)

```
async def _complete(self, session: str) -> YcAuthResult
```

**Purpose**: This checks whether the admin has finished approving the YC sign-in. If approval is complete, it saves the YC tokens as the extension’s credential.

**Data flow**: It reads the pending authorization record. If none exists, it checks whether credentials are already present and reports connected if they are. If a pending flow exists, it reopens the sealed device code, verifies it has not expired, asks YC’s token endpoint for credentials, and then either stores the credentials, reports that approval is still pending, or raises a clear error.

**Call relations**: This is called by `YcAuth.run` for the `complete` action. It depends on `_credential_digest` to detect credentials that were completed elsewhere, `_headers` to identify the request to YC, and `_bound` to keep auth responses within a safe size.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 4 external calls (__init__, __init__, loads, time).


##### `YcAuth._credential_digest`  (lines 231–239)

```
async def _credential_digest(self) -> str | None
```

**Purpose**: This creates a safe fingerprint of the currently stored YC credentials. The fingerprint lets the code tell whether credentials changed without comparing or exposing the tokens directly.

**Data flow**: It tries to read the YC credential slot. If the slot is unset, it returns nothing. If credentials exist, it validates that they have the expected shape, hashes the raw credential text with SHA-256, and returns the hash string.

**Call relations**: The start and complete auth steps use this helper to detect whether a pending authorization has become obsolete because credentials were already added or changed.

*Call graph*: called by 2 (_complete, _start); 1 external calls (sha256).


##### `YcAuth._headers`  (lines 241–246)

```
def _headers(self, session: str) -> dict[str, str]
```

**Purpose**: This builds the standard HTTP headers sent to YC’s auth service. The headers identify the CLI version and tie the request to a UFO conversation session.

**Data flow**: It receives a session string and returns a small dictionary of header names and values. It does not read or change stored state.

**Call relations**: Both `_start` and `_complete` call this before making HTTP requests to YC’s authorization endpoints.

*Call graph*: called by 2 (_complete, _start).


##### `YcAuth._bound`  (lines 248–250)

```
def _bound(self, response: httpx.Response) -> None
```

**Purpose**: This protects UFO from unexpectedly huge YC auth responses. It is a safety check before the response body is parsed.

**Data flow**: It receives an HTTP response and measures the response content. If it is larger than the configured maximum, it raises a YC CLI error; otherwise it returns without changing anything.

**Call relations**: The start and complete auth steps call this immediately after receiving a response from YC, before interpreting success, failure, or token data.

*Call graph*: called by 2 (_complete, _start); 1 external calls (__init__).


##### `_read_bounded`  (lines 253–261)

```
async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes
```

**Purpose**: This reads output from a running process while enforcing a maximum size. It prevents the YC CLI from filling memory with unlimited output or error text.

**Data flow**: It receives a stream and a byte limit. It reads the stream in chunks, counts the bytes, and either returns all collected bytes or raises an error once the limit is exceeded.

**Call relations**: `YcCli._execute` uses this for both standard output and standard error while the external `yc` program is running.

*Call graph*: called by 1 (_execute); 2 external calls (__init__, read).


##### `YcCli.run`  (lines 269–279)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This runs one YC CLI command using stored credentials in a temporary, private environment. It is the main safe wrapper around the external `yc` executable.

**Data flow**: It reads the saved credential text, validates it, creates a temporary home folder containing those credentials, runs the requested command, then checks whether the CLI refreshed the credentials. Finally, it removes the temporary folder and returns the command’s text output.

**Call relations**: YcRead uses this through the runner interface. Internally it prepares the temporary home, calls `_execute` to run the command, calls `_persist_refresh` afterward, and always cleans up the temporary files.

*Call graph*: calls 2 internal fn (_execute, _persist_refresh); 1 external calls (to_thread).


##### `YcCli._prepare_home`  (lines 281–288)

```
def _prepare_home(self, raw: str) -> Path
```

**Purpose**: This creates the private temporary home folder that makes the YC CLI see the right credentials. It keeps credentials isolated from the real host user account.

**Data flow**: It receives raw credential JSON. It creates a new temporary directory, locks down its permissions, writes the credentials to `.yc/credentials.json`, locks down that file, and returns the directory path.

**Call relations**: `YcCli.run` calls this before executing the CLI. The returned folder becomes the `HOME` environment variable for the subprocess.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `YcCli._execute`  (lines 290–331)

```
async def _execute(self, home: Path, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This actually launches the external `yc` command and captures its result. It enforces time limits, output limits, and clear error reporting.

**Data flow**: It receives the temporary home folder, command arguments, and a session label. It starts the `yc` executable with a controlled environment, reads standard output and standard error with size limits, waits up to the configured timeout, and returns decoded text if the command succeeds. If the CLI is missing, too slow, too noisy, or exits with an error, it raises a YC CLI error.

**Call relations**: `YcCli.run` calls this after preparing credentials. This function delegates stream reading to `_read_bounded` so both normal output and error output are capped safely.

*Call graph*: calls 1 internal fn (_read_bounded); called by 1 (run); 5 external calls (__init__, create_subprocess_exec, create_task, gather, timeout).


##### `YcCli._persist_refresh`  (lines 333–352)

```
async def _persist_refresh(self, home: Path, expected: str) -> None
```

**Purpose**: This saves refreshed YC credentials back to UFO when the CLI updates them. It is careful not to overwrite someone else’s newer credential change by accident.

**Data flow**: It reads the credentials file from the temporary home folder after the CLI finishes. If nothing changed, it does nothing. If the file changed, it tries to rotate the stored credential from the expected old value to the refreshed value. If another update happened first, it compares creation times and retries only when the refreshed credential is newer.

**Call relations**: `YcCli.run` calls this after every CLI execution, even if cleanup is about to happen. It is the bridge from the CLI’s local token refresh behavior back into UFO’s shared credential store.

*Call graph*: called by 1 (run); 2 external calls (__init__, to_thread).


##### `YcReadInput.validate_action`  (lines 365–372)

```
def validate_action(self) -> 'YcReadInput'
```

**Purpose**: This checks that a YC read request contains the fields required for its chosen action. It catches mistakes before a bad CLI command is built.

**Data flow**: It reads the parsed input fields. It requires a query for `ask` and `search`, requires a skill name for `skills_read`, and only allows `entity` with `search`. If the combination is valid, it returns the input object; otherwise it raises a validation error.

**Call relations**: This runs when tool input is parsed into `YcReadInput`. It ensures `YcRead.run` can translate the request without guessing what the user meant.


##### `YcRead.run`  (lines 379–394)

```
async def run(self, args: YcReadInput, session: str) -> str
```

**Purpose**: This converts a high-level YC read action into the exact command-line arguments expected by the `yc` program. It is the translator between UFO tool input and YC CLI commands.

**Data flow**: It receives validated read input and a session label. It chooses a command such as `agent`, `search`, `skills list`, `skills read`, or `tools context`, adds JSON flags or search type filters where needed, and sends the command to its runner. It returns the runner’s text output.

**Call relations**: The `yc_read` tool calls this for user-facing YC lookups. This function then hands the final command to a `YcRunner`, normally `YcCli`, which performs the actual subprocess work.


##### `yc_read`  (lines 397–403)

```
async def yc_read(ctx: ToolContext, args: YcReadInput) -> ToolResult
```

**Purpose**: This is the tool function UFO calls when a user wants to read or query YC information. It packages the YC CLI result into UFO’s standard tool response format.

**Data flow**: It receives the tool context and validated YC read input. It builds a `YcCli` from the extension’s credential access, wraps it in `YcRead`, runs the requested action with a conversation-based session label, and returns the output as text content inside a tool result.

**Call relations**: This is the external-facing entry for read operations in this file. It creates the concrete pieces, then lets `YcRead.run` and `YcCli.run` do the translation and command execution.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `yc_auth`  (lines 406–413)

```
async def yc_auth(ctx: ToolContext, args: YcAuthInput) -> ToolResult
```

**Purpose**: This is the tool function UFO calls when an admin starts or completes YC authorization. It returns a small JSON message saying whether the account is connected, still pending, or needs user approval.

**Data flow**: It receives the tool context and auth input. It opens an HTTP client with a short timeout, creates `YcAuth`, runs the requested auth action with a conversation-based session label, and returns the auth result as JSON text inside a tool result.

**Call relations**: This is the external-facing entry for authorization in this file. It wires the tool call to `YcAuth.run`, while `YcAuth` performs the permission checks and YC auth-server communication.

*Call graph*: 4 external calls (__init__, __init__, __init__, AsyncClient).

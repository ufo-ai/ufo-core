# Pipedream Brokered Connector Integration  `stage-13.4`

This stage is shared behind-the-scenes support for using outside apps through Pipedream, a hosted service that manages app connections for us. It lets UFO offer actions from tools like Gmail or Linear without storing the user’s secret login tokens.

The client is the main messenger to Pipedream Connect. It creates account-linking links, checks that a connected account really belongs to the expected user or workspace, lists available app actions, and asks Pipedream to run them. The provider plugs this into UFO’s normal “connect an account” flow. Because Pipedream’s login process happens on its own site and finishes later, the provider acts like a careful handoff point.

The broker connects Pipedream’s action catalog to UFO’s connector system. It helps search for actions, explain what inputs they need, run the chosen action with the right connected account, and gather any files the action creates. The proxy supports direct provider API calls. It wraps a normal web request, sends it through Pipedream so the secret token stays hidden, and returns the provider’s real response.

## Files in this stage

### Connector Broker Facade
The broker exposes Pipedream catalog search, action description, execution, account selection, and output collection to UFO's connector system.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`io_transport` · `request handling`

Pipedream offers many prebuilt actions for apps such as Gmail, Slack, and others. UFO needs a safe, predictable way to expose those actions to an agent without making the agent understand Pipedream's internal details. This file provides that shared broker.

Think of it like a concierge desk. The agent asks, “What can this app do?”, “What information does this action need?”, or “Run this action using this account.” The broker translates those requests into Pipedream API calls, checks that the connected account really belongs to the requested app, adds the account into the hidden app-authentication slot, and then sends the action to Pipedream to run.

It also cleans up the shape of Pipedream data. Pipedream action definitions include internal fields that the agent should not fill in, such as the connected app account slot. The broker removes those and returns a normal JSON schema, which is a machine-readable description of allowed input fields. If an action name is wrong, it tries to suggest close real action names rather than failing silently. If an old connected account no longer works, it adds guidance telling the user to reconnect.

File handling is intentionally asymmetric: action outputs may include downloadable file URLs, but uploads are refused because Pipedream actions expect file inputs as URLs rather than staged uploads.

#### Function details

##### `PipedreamBroker.tools`  (lines 62–64)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Looks up Pipedream actions for one provider, such as a particular app, and returns them as UFO broker tools. Someone uses this when the agent needs to discover what actions are available.

**Data flow**: It receives a workspace id, a provider name, and a search query. It finds the Pipedream app slug for that provider, asks the current Pipedream client for matching actions, then converts Pipedream's raw action rows into BrokerTool objects. It returns a tuple of usable tool descriptions.

**Call relations**: This is the discovery path used directly by search. It relies on _spec to translate the provider into a Pipedream app and on _listed_tools to turn Pipedream's catalog rows into the common broker format.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 66–73)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the detailed input shape for one Pipedream action. The result tells the agent which arguments it may provide and which ones are required.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts its configurable properties, removes fields that belong to Pipedream or the broker, and builds a BrokerTool containing the action description, input schema, and read-only hint. It returns that single tool description.

**Call relations**: This is called when the system already knows an action key and wants its exact calling instructions. It gets the raw definition through _definition, then uses _props, _input_schema, _read_only, and _str to turn that raw definition into a safe public schema.

*Call graph*: calls 5 internal fn (_definition, _input_schema, _props, _read_only, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 75–110)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It is the main execution path that binds the user's account, checks it matches the requested app, and reports useful errors if something is stale or misspelled.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, connected account id, and an optional idempotency key. It fetches the action definition, copies the arguments, inserts the connected account into the action's hidden app slot, verifies that the account belongs to the requested workspace and app, then asks Pipedream to run the action. It returns the Pipedream response, or raises a clear error if the action is unknown, the account is stale, the app does not match, or the action itself reports failure.

**Call relations**: This is the broker's central run flow. It calls _definition first, falls back to _key_miss if the action slug is unknown, uses _app_slot to know where to attach the account, uses _spec to check the app, and uses _stale_account plus _reconnect_error to turn stale-grant failures into reconnect guidance.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 112–130)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files that a Pipedream action saved and exposes them as downloadable broker files. This lets the sandbox fetch output files without knowing Pipedream's internal file stash format.

**Data flow**: It receives a Pipedream action response. It looks inside the response exports for the special file-stash upload list, skips malformed entries, extracts each download URL, and derives a simple filename from the local path if available. It returns a tuple of BrokerFile objects.

**Call relations**: This runs after execute has produced a response and the caller wants to collect file results. It does not call back into Pipedream; it only translates the response's file metadata into the shared broker file format.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 132–144)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Refuses staged file uploads for Pipedream actions. This is deliberate because Pipedream expects file inputs to be URLs, not broker-managed upload slots.

**Data flow**: It receives upload details such as workspace, provider, action slug, filename, MIME type, and checksum. Instead of creating an upload location, it raises an error explaining that the file should be shared and passed as a download URL. Nothing is uploaded or changed.

**Call relations**: This exists to satisfy the common broker interface while making Pipedream's different file-input model explicit. Callers that need to pass a file must use the system's share_file flow before calling the Pipedream action.


##### `PipedreamBroker.search`  (lines 146–147)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Pipedream actions and returns them in the common broker search wrapper. Pipedream has no separate planning router here, so this is essentially tool discovery packaged as a search result.

**Data flow**: It receives a workspace id, provider, and query. It asks tools for matching actions, wraps those tools in a BrokerSearch object, and returns it. There is no extra guidance or routing plan added.

**Call relations**: This is a thin wrapper around PipedreamBroker.tools. It is used when the broader connector system expects a BrokerSearch result rather than just a tuple of tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 149–170)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Builds a credential object that can send requests through Pipedream's Connect Proxy for a verified connected account. This lets code talk to an app through the user's existing Pipedream connection.

**Data flow**: It receives a workspace id, provider, and account id. It looks up the provider's Pipedream app, fetches the connected account for that workspace, turns a missing account into reconnect guidance, rejects accounts for the wrong app, and then returns a Credential containing a PipedreamProxyTransport. The returned transport knows the account id and external user id needed for proxied requests.

**Call relations**: This is used when a caller needs authenticated app access rather than running a predefined action. It depends on _spec for the expected app, the Pipedream client for account lookup, and PipedreamProxyTransport to do the actual proxied HTTP transport work.

*Call graph*: calls 3 internal fn (__init__, _spec, __init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, pipedream_client).


##### `PipedreamBroker._definition`  (lines 172–180)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the raw definition for one Pipedream action. It turns Pipedream's “not found” response into UFO's UnknownBrokerTool error so higher-level code can react consistently.

**Data flow**: It receives an action slug. It asks the current Pipedream client for that action's definition, unwraps the nested data object if Pipedream returned one, and returns a dictionary definition. If Pipedream says the action does not exist, it raises UnknownBrokerTool.

**Call relations**: schema uses this to describe an action, and execute uses it before running an action. When execute catches UnknownBrokerTool, it asks _key_miss to produce a more helpful missing-action error.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 182–201)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Creates a helpful error for a misspelled or unknown action key. Instead of only saying “not found,” it suggests nearby real action keys when possible.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It finds the provider's app, tries to list all actions for that app, converts them to broker tools, compares their slugs with the missing one, and builds a PipedreamError containing either close matches or advice to search the catalog. It returns that error object for the caller to raise.

**Call relations**: execute calls this only after _definition reports an unknown action. It uses _spec to know which app catalog to search and _listed_tools to normalize the catalog before comparing action slugs.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute); 1 external calls (get_close_matches).


##### `_stale_account`  (lines 204–211)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Detects whether a Pipedream failure probably means the connected account is no longer usable. This helps the system distinguish “the user should reconnect” from ordinary provider errors.

**Data flow**: It receives a PipedreamError and an account id. It lowercases the error body and checks for narrow signs of a stale grant, such as “external user not found” or the account id appearing with “not found.” It returns true or false.

**Call relations**: execute uses this when a run or action-level error occurs. If it returns true, execute passes the error through _reconnect_error so the user gets reconnection guidance.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 214–215)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds user-facing reconnection advice to an existing Pipedream error. It keeps the original status but makes the message more actionable.

**Data flow**: It receives a PipedreamError and provider name. It appends stale-grant guidance for that provider to the error body and returns a new PipedreamError with the same status code. The original error is not changed.

**Call relations**: execute calls this after _stale_account identifies a stale connected account. It uses stale_grant_guidance to produce the standard message telling the agent or user what to do next.

*Call graph*: calls 1 internal fn (__init__); called by 1 (execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 218–222)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up UFO's registered Pipedream connector specification for a provider name. This is how the broker knows which Pipedream app belongs to a provider.

**Data flow**: It receives a provider string. It checks the Pipedream connector registry and returns the matching ConnectorSpec. If no provider is registered, it raises a KeyError.

**Call relations**: tools, execute, credential, and _key_miss all use this before talking to Pipedream. It is the shared provider-to-app translation point for the file.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 225–242)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream catalog rows into UFO BrokerTool objects. It filters out unusable rows and exposes only the action key, description, safe input schema, and read-only hint.

**Data flow**: It receives raw action rows from Pipedream. For each row with a non-empty string key, it builds a BrokerTool using the row's description, configurable properties, and annotations. It returns a tuple of normalized tools.

**Call relations**: tools uses this for normal discovery, and _key_miss uses it when searching for close action-name suggestions. It relies on _props, _input_schema, _read_only, and _str to clean up each raw Pipedream row.

*Call graph*: calls 4 internal fn (_input_schema, _props, _read_only, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_read_only`  (lines 245–247)

```
def _read_only(definition: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Pipedream action is marked as read-only. A read-only action is expected to look up information rather than change something.

**Data flow**: It receives an action definition. It looks for an annotations dictionary and checks whether readOnlyHint is exactly true. It returns a boolean.

**Call relations**: schema and _listed_tools call this while building BrokerTool objects. The read-only flag then travels with the tool description so callers can reason about the action's risk.

*Call graph*: called by 2 (schema, _listed_tools).


##### `_props`  (lines 250–252)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable input properties from a Pipedream action definition. It also drops malformed entries so later code can work with a clean list.

**Data flow**: It receives an action definition. It reads configurable_props, keeps only entries that are dictionaries, and returns them as a list. If the field is missing or not a list, it returns an empty list.

**Call relations**: schema and _listed_tools use this before building public input schemas. _app_slot also uses it to find the hidden account-binding field needed during execution.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 255–262)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream input field where the connected account must be inserted. Without this slot, the broker cannot run the action on behalf of the user's account.

**Data flow**: It receives an action definition and slug. It scans the action's configurable properties for a property whose type is the Pipedream app-account type and whose name is a non-empty string. It returns that property name, or raises a PipedreamError if no such slot exists.

**Call relations**: execute calls this after loading the action definition. The returned field name is where execute places the account's authProvisionId before sending the action to Pipedream.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 265–288)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the public JSON schema for action arguments the agent is allowed to provide. It hides broker-owned and Pipedream-internal properties so the agent only sees real user inputs.

**Data flow**: It receives a list of Pipedream configurable properties. It skips missing names, the hidden app account slot, directory/service fields, and internal fields whose type starts with a dollar sign. For the remaining properties, it maps Pipedream property types to JSON schema types, adds descriptions, records required fields, and returns an object-shaped schema.

**Call relations**: schema uses this when describing one action, and _listed_tools uses it when showing catalog results. It calls _str to safely read property types even when Pipedream data is not perfectly shaped.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 291–292)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only if it already is one. It prevents accidental non-string values from leaking into descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not modify anything.

**Call relations**: schema, _listed_tools, and _input_schema use this small helper while cleaning Pipedream's raw data. It keeps the rest of the broker code simple and defensive when external API fields are missing or oddly typed.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### Hosted Account Linking
The client and provider manage Pipedream Connect account authorization, validation, action listing, and safe action execution through hosted OAuth flows.

### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector OAuth, account lookup, and connector action execution`

This file is the bridge between UFO and Pipedream Connect. Pipedream is used as a trusted middleman for OAuth, which is the common “sign in and allow access” flow used by services like Google. The important safety idea is that UFO stores only a Pipedream connected-account id, not the user’s real provider token.

The file starts by naming the Pipedream settings it needs from the environment and by defining the small set of connectors this broker supports. These are providers where Pipedream fills a gap that another broker does not cover.

The main class, PipedreamClient, talks to Pipedream’s REST API over HTTP. Before it can call most Pipedream endpoints, it gets a short-lived access token using the deployment’s client id and secret, then caches that token so every request does not need a fresh login. It can mint a Connect Link for browser-based consent, look up connected accounts, list or describe available actions, and run an action on Pipedream’s servers.

Several helper functions are guards. They check that an account really belongs to the expected external user or workspace. This matters because the project-level Pipedream token can read accounts across the project; without these checks, one user could accidentally or maliciously act through another user’s connection. Other helpers normalize API responses and turn bad responses into loud errors instead of quiet, misleading empty results.

#### Function details

##### `PipedreamError.__init__`  (lines 90–93)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error when Pipedream fails or returns data this code cannot safely use. It keeps the HTTP status and response body so callers can understand what went wrong.

**Data flow**: It receives a status code and response text → formats them into a readable exception message → stores both pieces on the error object for later inspection.

**Call relations**: This error is raised throughout the Pipedream broker and this client when something is unsafe or unexpected, such as a missing token, a forbidden account, or a bad API response. It is the common way this subsystem fails loudly instead of pretending a broken grant or malformed response is usable.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 131–152)

```
async def access_token(self) -> str
```

**Purpose**: Gets the deployment’s own Pipedream API access token, which is needed before making authenticated Pipedream calls. It reuses a cached token until it is close to expiring, saving repeated login requests.

**Data flow**: It reads the client id from the client object and checks the process-wide token cache → if a still-valid token exists, it returns it → otherwise it posts the client id and secret to Pipedream’s OAuth endpoint, validates the response, stores the new token with its expiry time, and returns the token string.

**Call relations**: The lower-level request helpers, PipedreamClient._get and PipedreamClient._post, call this before talking to authenticated Pipedream endpoints. It uses PipedreamClient._http to create the temporary HTTP client and _body to turn the HTTP response into a checked dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 154–169)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted consent link. A user’s browser opens that link so they can approve access to an outside app.

**Data flow**: It receives an external user id plus success and error redirect URLs → sends them to Pipedream → checks that Pipedream returned both a token and a connect-link URL → returns them as a ConnectToken object.

**Call relations**: This is used during the OAuth connection flow, before any provider action can run. It relies on PipedreamClient._post for the authenticated API request and raises PipedreamError if the returned consent information is incomplete.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 171–178)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Fetches one connected account and proves it belongs to the expected external user. This prevents the project’s broad Pipedream credentials from being used as a confused deputy for the wrong user.

**Data flow**: It receives a Pipedream account id and the external user id that should own it → fetches the account record from Pipedream → unwraps the response if Pipedream nested it under data → checks ownership and health → returns a ConnectedAccount summary.

**Call relations**: This is part of the grant-validation path after an account id is known. It delegates response cleanup to _dict and the important ownership check to _owned_account.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 180–184)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a human-friendly name for a connected account, if Pipedream has one. This is useful for showing users which account they connected.

**Data flow**: It receives an account id → fetches the account from Pipedream → unwraps the record if needed → reads the name field → returns the name string, or None if there is no usable name.

**Call relations**: It uses the same authenticated read path as other account lookups through PipedreamClient._get. Unlike connected_account and workspace_account, it only reads a label and does not perform ownership validation itself.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 186–196)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Fetches a connected account and checks that it belongs to the given workspace. This is the workspace-level safety gate before execution is allowed.

**Data flow**: It receives an account id and workspace UUID → fetches the account record from Pipedream → turns it into a ConnectedAccount → checks whether the account’s external user id fits the workspace’s allowed naming pattern → returns the account or raises an error if it belongs elsewhere.

**Call relations**: This function is used when execution needs to be limited to accounts connected under a specific workspace. It calls _account to validate the account itself, then _workspace_owns_external_user to verify workspace ownership.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 198–212)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently connected account for a specific external user and app. This is useful right after a consent flow finishes, when the system needs to identify which Pipedream account was just created.

**Data flow**: It receives an external user id and app slug → asks Pipedream for matching accounts → keeps only dictionary-shaped records → chooses the one with the newest creation timestamp → checks that it has an id and belongs to the external user → returns a ConnectedAccount.

**Call relations**: This sits at the end of the browser consent flow, connecting Pipedream’s callback state to the account that was created. It uses PipedreamClient._get for the listing, _dict for safe response handling, and _owned_account for the ownership guard.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 214–243)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the Pipedream actions available for an app, optionally filtered by search text. It follows pages of results so discovery can see actions beyond the first page.

**Data flow**: It receives an app slug and optional query → repeatedly requests pages of actions from Pipedream → adds valid action records to a list → stops when the page is short, there is no next cursor, or the configured maximum is reached → returns the collected actions as an immutable tuple.

**Call relations**: The broker calls this when it needs to search for a missing or discoverable action key. It uses PipedreamClient._get for each page and _dict to safely read Pipedream’s page information.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 245–246)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition for one Pipedream action component. Callers use this to learn what inputs an action expects before trying to run it.

**Data flow**: It receives an action key → requests that component from Pipedream → returns the checked response dictionary.

**Call relations**: This is a thin catalog lookup. It hands the work to PipedreamClient._get, which adds authentication and response validation.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 248–266)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action on Pipedream’s servers for a specific external user and set of configured inputs. It also asks Pipedream to use a fresh file stash so files produced by the action can be downloaded later.

**Data flow**: It receives an action key, external user id, and configured properties → builds the run request body with a new stash id → checks the JSON size so huge arguments are rejected before sending → posts the request to Pipedream → returns Pipedream’s response dictionary.

**Call relations**: This is the execution path after a grant and action have been chosen. It uses PipedreamClient._post to perform the authenticated run request; if arguments are too large, it stops locally with ValueError before any network call.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 268–271)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated HTTP GET request to Pipedream and returns a checked dictionary response. It is the shared read helper for account, action, and catalog lookups.

**Data flow**: It receives an API path and optional query parameters → gets an access token → opens a short-lived HTTP client with that token → sends the GET request → passes the response through _body → returns the parsed dictionary.

**Call relations**: Higher-level methods like connected_account, workspace_account, newest_account, list_actions, action_definition, and account_label call this instead of each building their own HTTP request. It relies on access_token for authentication and _http for client setup.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 6 (account_label, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 273–276)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated HTTP POST request to Pipedream and returns a checked dictionary response. It is the shared write-or-run helper for creating connect tokens and running actions.

**Data flow**: It receives an API path and a JSON-ready body → gets an access token → opens a short-lived HTTP client with that token → sends the POST request → validates and parses the response through _body → returns the parsed dictionary.

**Call relations**: PipedreamClient.connect_token and PipedreamClient.run_action call this for their network work. It keeps authentication, HTTP setup, and response checking in one place.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 278–291)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates a temporary asynchronous HTTP client pointed at Pipedream’s API. For authenticated calls, it adds the bearer token and Pipedream environment header.

**Data flow**: It receives an optional access token → if a token is present, builds headers with authorization and environment → creates an httpx AsyncClient with the base URL, timeout, optional test transport, and headers → returns that client to be used in an async context.

**Call relations**: PipedreamClient.access_token uses it without a token for the OAuth-token request. PipedreamClient._get and PipedreamClient._post use it with a token for normal Pipedream Connect API calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 294–295)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This prevents crashes or accidental assumptions when Pipedream returns an unexpected shape.

**Data flow**: It receives any value → checks whether it is a dictionary → returns the value unchanged if so, otherwise returns an empty dictionary.

**Call relations**: Account and action-reading code calls this before reading nested fields such as data, app, or page_info. It is a small safety helper used by PipedreamClient account methods, list_actions, and _account.

*Call graph*: called by 6 (account_label, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 298–311)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that a Pipedream account record belongs to the exact external user expected. It turns the raw account record into a safe ConnectedAccount only after that ownership check passes.

**Data flow**: It receives a raw account record, account id, and expected external user id → asks _account to validate and extract the account → compares the account’s owner with the expected owner → returns the account if they match or raises PipedreamError if they do not.

**Call relations**: PipedreamClient.connected_account and PipedreamClient.newest_account call this whenever they must bind an account to a specific external user. It builds on _account, which checks the record’s basic health and required fields.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 314–337)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Converts a raw Pipedream account record into the small ConnectedAccount shape this project uses, while refusing accounts that cannot authenticate. It treats an unhealthy grant as something the user must reconnect, not as a server bug to retry forever.

**Data flow**: It receives a raw account record and account id → reads the external owner field → rejects the record if the owner is missing → rejects unhealthy accounts by raising GrantUnusable → reads the app slug if present → returns a ConnectedAccount with the account id, app, and owner.

**Call relations**: This is the basic account-projection helper used by _owned_account and PipedreamClient.workspace_account. It calls _dict to safely inspect nested app data and raises either PipedreamError for malformed records or GrantUnusable for revoked or broken user grants.

*Call graph*: calls 3 internal fn (__init__, __init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 340–341)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used for Pipedream external user ids that belong to a workspace. This gives the code a consistent naming scheme for workspace-owned connection users.

**Data flow**: It receives a workspace UUID → converts it to its compact hexadecimal form → returns a string beginning with the project’s external-user prefix and ending in an underscore.

**Call relations**: _workspace_owns_external_user uses this to recognize account owners that belong to a workspace. connection_user_id uses it when creating a new external user id for a connection flow.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 344–353)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user id belongs to a given workspace. It recognizes both an older simple format and the newer workspace-plus-connection-id format.

**Data flow**: It receives a workspace UUID and an external user id → first checks the legacy exact id form → otherwise checks for the workspace prefix → extracts the connection id after the prefix → returns true only if that id is the expected length and made of lowercase hexadecimal characters.

**Call relations**: PipedreamClient.workspace_account calls this after reading an account from Pipedream. It uses workspace_user_prefix so the same workspace id format is shared by validation and id creation.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 356–358)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for one workspace connection flow. It derives the connection part from the OAuth state string, so the id is deterministic without storing a secret.

**Data flow**: It receives a workspace UUID and state string → hashes the state with SHA-256, a one-way fingerprint function → takes the configured number of hex characters → appends that to the workspace user prefix → returns the external user id.

**Call relations**: This is used when starting or correlating a connection flow that should be scoped to a workspace. It shares the same prefix format that _workspace_owns_external_user later checks.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 361–369)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe dictionary or raises a clear error. It centralizes the rule that bad status codes and non-object JSON are not acceptable.

**Data flow**: It receives an httpx response → if the status code is 400 or higher, raises PipedreamError with the response text → if there is no body, returns an empty dictionary → otherwise parses JSON and checks it is a dictionary → returns that dictionary or raises PipedreamError for any other shape.

**Call relations**: PipedreamClient.access_token, PipedreamClient._get, and PipedreamClient._post all call this after receiving HTTP responses. Because it is shared, every Pipedream API call gets the same strict response handling.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 372–390)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds the default PipedreamClient from environment variables. It fails immediately if the deployment is missing the credentials or project id needed to broker OAuth.

**Data flow**: It reads the Pipedream client id, client secret, project id, and optional environment from process environment variables → checks that the required three values are present → creates and returns a PipedreamClient configured for this deployment.

**Call relations**: Other parts of the Pipedream extension use this as the normal way to obtain a client. It does not make a network call itself; it prepares the client object that later methods use for consent, account lookup, catalog reads, and action runs.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `OAuth connection request handling`

This file solves a mismatch between two systems. ufo expects an OAuth provider to give it a normal web address where the user can approve access, then later exchange a returned code for an account. Pipedream works differently: before sending the user to its hosted Connect page, the server must first make an asynchronous API call to create a short-lived Connect token. Because a normal `authorize_url` method cannot do that asynchronous work, this file points the browser to ufo’s own extension route first.

The flow is like a receptionist redirecting a visitor. First, `PipedreamOAuthProvider.authorize_url` sends the browser to `/ext/pipedream/oauth` with the provider name, state, and callback address. Then `oauth_route` creates the Pipedream Connect token, builds a Pipedream Connect Link for the correct app, and redirects the user there. When Pipedream sends the browser back, the same route checks whether consent succeeded. If it did, it finds the newest connected Pipedream account for this exact ufo workspace and sealed state, then redirects back to ufo core with that account id as the code.

Finally, `PipedreamOAuthProvider.exchange` verifies that the account really belongs to the expected Pipedream app before returning an `OAuthAccount`. This matters because several browser callbacks could happen close together; the code carefully ties each flow to its own generated Pipedream “external user” so one connection cannot accidentally claim another flow’s account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 51–53)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL the user’s browser should visit when starting a Pipedream-backed connection. Instead of sending the user straight to Pipedream, it sends them to this extension’s own bridge route so the server can create the needed Pipedream token first.

**Data flow**: It receives a sealed `state` value and a `redirect_uri` from ufo core. It extracts the origin, meaning the scheme and host such as `https://example.com`, from the callback URL, then adds the provider name, state, and callback as query parameters. It returns a complete URL pointing to `/ext/pipedream/oauth` on that same origin.

**Call relations**: This is the first step in the connection story. It calls `_origin` to make sure the callback has a usable web origin, and uses URL encoding so the browser can safely carry the state and callback values. The browser later reaches `oauth_route`, which performs the asynchronous Pipedream work that this method cannot do.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 55–69)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This turns the returned Pipedream account id into the `OAuthAccount` object that ufo core expects. It also double-checks that the account belongs to the right Pipedream app before ufo accepts it.

**Data flow**: It receives the returned `code`, which in this flow is really a Pipedream account id, along with the workspace id and sealed state. It rebuilds the Pipedream external user id for that exact workspace and state, asks Pipedream for that connected account, and checks that the account’s app matches this provider. It then tries to fetch a human-friendly account label and returns an `OAuthAccount` containing the account id and optional label.

**Call relations**: This runs after the browser has come back through `oauth_route` and ufo core is ready to bind the connection. It calls Pipedream client helpers to look up the exact account and raises a Pipedream error if the account belongs to another app. Its result is handed back to ufo core as the connected account record, while the real token stays stored inside Pipedream.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 72–120)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge for the Pipedream consent process. It starts the hosted Pipedream connection flow, receives the user back afterward, and reports success or failure to ufo core.

**Data flow**: It reads query parameters from the incoming request: the provider, sealed state, callback URL, and optionally an outcome from Pipedream. If required values are missing, it returns an error response. If the provider is unknown, it returns a not-found response. If Pipedream reports success, it finds the newest account for this workspace, state, and app, then redirects back to ufo core with the state and account id. If Pipedream reports failure, it returns a clear failure page. If there is no outcome yet, it creates a Pipedream Connect token, builds a hosted Connect Link for the requested app, optionally adds a custom OAuth app id from the environment, and redirects the browser to Pipedream.

**Call relations**: This is reached when `PipedreamOAuthProvider.authorize_url` sends the user’s browser to the extension route. It calls `_origin` when building return URLs, uses the connector registry to find the Pipedream app slug, and calls the Pipedream client to create tokens or locate accounts. On success it hands control back to ufo core through the callback URL, so core can call `PipedreamOAuthProvider.exchange` and finalize the connection.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 123–127)

```
def _origin(url: str) -> str
```

**Purpose**: This extracts the safe base web address from a full URL. It is used so redirects stay on the same scheme and host as the callback, such as `https://server.example`, without carrying over the full path.

**Data flow**: It receives a URL string and parses it into pieces. If the URL is not an HTTP or HTTPS URL, or if it has no host, it raises an error because the OAuth bridge cannot safely build a browser redirect. Otherwise it returns only the scheme and network location, for example `https://example.com`.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` call this helper when they need to build bridge URLs. It keeps that validation and URL trimming in one small place, so the rest of the flow can rely on having a usable origin for redirects.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Provider Request Proxying
The proxy forwards third-party provider HTTP requests through Pipedream Connect without exposing or storing provider secrets.

### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and connection teardown`

Many data sources need to call services like Gmail or another provider, but Pipedream keeps the real provider credential on its own servers. This file solves that gap. It acts like a mail-forwarding service: the connector writes a request as if it were sending it straight to the provider, but this transport repackages the request and sends it to Pipedream instead. Pipedream adds the account credential on the server side, calls the provider, and returns the provider's response.

The main class, PipedreamProxyTransport, plugs into httpx, a Python HTTP client library. When a request comes in, it first asks the Pipedream client for an access token. It reads the original request body, builds Pipedream authentication headers, and carefully copies only the headers that should be forwarded to the real provider. Pipedream requires forwarded provider headers to start with a special prefix, so this code adds that prefix when needed. It also skips transport-level headers such as content length, host, cookies, and authorization, because forwarding those could be wrong or unsafe.

The original provider URL is encoded into the proxy path, and the Pipedream account and external user are added as query parameters. The response is not interpreted here. That is important: status codes, headers, and body pass back as-is, so the rest of the connector can behave as though it talked directly to the provider.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 55–74)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This function turns one ordinary provider HTTP request into a Pipedream proxy request. It is used whenever the connector wants to fetch or send data to the provider without directly holding the provider's credential.

**Data flow**: It receives an httpx request aimed at the provider. It asks the Pipedream client for a Pipedream access token, reads the request body, filters and re-prefixes safe provider headers, encodes the original URL into the proxy URL, and adds the account and external user identifiers. It then creates a new request to Pipedream and sends it through the inner transport. The output is the HTTP response returned by Pipedream, which represents the provider's response.

**Call relations**: This is the transport hook httpx calls when a request is sent through a client using PipedreamProxyTransport. Inside that moment, it relies on the Pipedream client for authentication, uses standard httpx request and URL objects to build the proxy call, and hands the finished proxy request to the wrapped inner transport for the actual network work.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 76–77)

```
async def aclose(self) -> None
```

**Purpose**: This function shuts down the wrapped HTTP transport. It is used when the client is finished so network resources can be cleaned up properly.

**Data flow**: It receives no new data beyond the transport object itself. It tells the inner transport to close, which releases whatever connections or background resources that transport owns. It returns nothing.

**Call relations**: This is called during cleanup for an httpx client using this transport. PipedreamProxyTransport does not own separate network machinery itself, so it passes the close request directly to the inner transport that performed the actual sending.

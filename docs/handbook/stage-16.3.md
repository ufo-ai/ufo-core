# Pipedream brokered tool integration  `stage-16.3`

This stage is the Pipedream connection layer. It is shared behind-the-scenes support used when a user connects an outside account, when the system looks up available actions, and when it runs those actions. Pipedream acts like a trusted middleman, so the project can use services such as Gmail without storing the user’s private provider token.

The client file is the main doorway to Pipedream Connect. It creates browser consent links, checks that connected accounts belong to the right user, lists actions Pipedream offers, and asks Pipedream to run them. The provider file plugs this into UFO’s normal account-connection flow, sending the user to Pipedream’s hosted OAuth page; OAuth is the standard “sign in and grant access” process. The broker file connects Pipedream actions to UFO’s tool system, so actions can be discovered, described, executed, and returned with any produced files. The proxy file rewrites ordinary service HTTP calls so they pass safely through Pipedream instead of exposing tokens. The package file simply makes these pieces importable.

## Files in this stage

### Connector broker facade
UFO-facing broker code exposes Pipedream actions to the connector system and packages the extension for import.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Pipedream provides ready-made actions for many apps, such as sending an email or creating a ticket. This broker makes those actions look like normal UFO connector tools. Think of it as a hotel concierge: the agent asks what services are available, the broker translates that into Pipedream's catalog, and when the agent chooses one, the broker books it using the member's connected account.

The broker is deliberately stateless. Each method asks for a fresh Pipedream client when it runs, so tests and runtime transport settings are respected and no old connection is accidentally reused. For discovery, it lists Pipedream actions for one provider and turns their configurable fields into a simple JSON schema. A JSON schema is a machine-readable description of what inputs are allowed.

When executing an action, the broker first loads the action definition, finds the hidden account slot, inserts the selected account, checks that the account really belongs to the requested app, and then calls Pipedream's server-side run API. It also improves error messages: unknown action names come back with real alternatives, and stale or missing account grants tell the user to reconnect. File output is handled through Pipedream's file stash URLs. Upload staging is refused because these actions expect file URLs, not uploaded blobs.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–61)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for a provider, such as Gmail or Slack, and returns them as UFO broker tools. Someone uses this when the agent needs to know what actions are available for an app.

**Data flow**: It receives a workspace id, provider name, and search text. It looks up the provider's Pipedream app slug, asks the Pipedream client for matching actions, then converts those raw catalog rows into clean BrokerTool objects. The result is a tuple of tools the agent can choose from.

**Call relations**: This is the basic discovery step. PipedreamBroker.search calls it when a broader search result is requested, and it relies on _spec to translate UFO's provider name and _listed_tools to shape Pipedream's catalog response.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 63–69)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input description for one specific Pipedream action. This tells the agent what arguments it may provide before trying to run the action.

**Data flow**: It receives a workspace id, provider name, and action slug. It loads the action definition, extracts its configurable properties, removes internal fields, and builds a BrokerTool with a JSON schema for the visible inputs. The output is one BrokerTool describing that action.

**Call relations**: This is used when the system already knows the action slug and needs its form. It asks _definition for the raw action data, then uses _props, _input_schema, and _str to turn that raw data into a safe, agent-facing schema.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 71–106)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a member's connected account. It also protects against common mistakes, such as using an action that does not exist or an account that belongs to the wrong app.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, account id, and optional idempotency key. It loads the action definition, copies the arguments, inserts the connected account into the action's hidden app slot, verifies that the account matches the provider, and sends the run request to Pipedream. It returns the action response, or raises a clear error if the action failed, the key was unknown, or the account needs reconnecting.

**Call relations**: This is the main run path. It calls _definition to understand the action, _app_slot to find where the account must be attached, _spec to verify the app, _key_miss to improve unknown-action errors, and _stale_account/_reconnect_error to turn stale account failures into reconnect guidance.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 108–126)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Pipedream action and presents them as downloadable broker files. This matters because actions may write files inside Pipedream's temporary runtime, and UFO needs stable URLs to fetch them.

**Data flow**: It receives the action response dictionary. It looks inside the response exports for Pipedream file stash upload records, keeps only valid records with download URLs, derives a friendly filename from the local path when possible, and returns BrokerFile objects. It does not download the files itself; it only reports where they can be fetched.

**Call relations**: This runs after an action has completed. It does not call other broker methods, but it understands the file stash export shape produced by Pipedream runs and converts it into UFO's BrokerFile format.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 128–140)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged file uploads for Pipedream actions. Pipedream actions expect file inputs as URLs, so the correct path is to share a workspace file and pass its download link.

**Data flow**: It receives workspace, provider, action, filename, mimetype, and checksum details that would normally describe an upload. Instead of creating an upload target, it raises a ValueError explaining that staged uploads are not supported here. Nothing is created or changed.

**Call relations**: This is called if the broader connector system asks the broker to prepare a file upload. For Pipedream, it stops that flow immediately and points callers toward the share_file URL approach.


##### `PipedreamBroker.search`  (lines 142–143)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for Pipedream tools and wraps the results in the standard broker search response. Pipedream has no separate planning or routing layer here, so the result is simply the matching tools.

**Data flow**: It receives a workspace id, provider, and query text. It calls tools with those same values, then places the returned tools into a BrokerSearch object. The output is a search response ready for the rest of UFO.

**Call relations**: This is a thin wrapper around PipedreamBroker.tools. It exists so Pipedream fits the same search interface as other connector brokers, even though all useful work is done by tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 145–166)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can send HTTP requests through Pipedream's Connect Proxy for one connected account. It first checks that the requested account exists in the workspace and belongs to the right app.

**Data flow**: It receives a workspace id, provider, and account id. It looks up the provider spec, loads the connected account from Pipedream, converts not-found errors into reconnect guidance, rejects accounts for the wrong app, and builds a Credential containing a PipedreamProxyTransport. The result is a transport-backed credential the caller can use for proxied app requests.

**Call relations**: This supports flows that need direct proxy access instead of running a predefined action. It uses _spec to know which app is expected and _reconnect_error when the account is missing, then hands the verified account details to PipedreamProxyTransport.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 168–176)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Loads the full definition for a Pipedream action. This is the source of truth for the action's description, inputs, and hidden account slot.

**Data flow**: It receives an action slug. It asks the Pipedream client for that action's definition, turns a 404 not found response into UnknownBrokerTool, and returns the definition dictionary, unwrapping a nested data field when needed. The output is raw action metadata for later shaping.

**Call relations**: PipedreamBroker.schema calls this to describe an action, and PipedreamBroker.execute calls it before running one. It is the shared lookup step before either presenting or executing a tool.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 178–192)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a more helpful error when an action slug is unknown during execution. Instead of only saying 'not found', it tries to include the real action keys available for that app.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider's app, tries to list all actions for that app, and formats a PipedreamError. If listing succeeds, the error includes available action names; if listing fails, it returns a plain not-found error.

**Call relations**: PipedreamBroker.execute calls this after _definition reports an unknown tool. It uses _spec to find the app and _listed_tools to clean the catalog rows before writing the suggestion-filled message.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 195–202)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream error likely means the connected account grant is stale or unknown. This lets the broker tell the user to reconnect only when the error really points to that problem.

**Data flow**: It receives a PipedreamError and the account id being used. It lowercases the error body and checks for narrow phrases such as 'external user not found' or the exact account id with 'not found'. It returns true when the error looks like a stale account, otherwise false.

**Call relations**: PipedreamBroker.execute uses this after run failures and action-level errors. When it returns true, execute passes the error to _reconnect_error so the final message explains how to fix the account connection.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 205–206)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds user-facing reconnect instructions to an existing Pipedream error. This turns a technical missing-account failure into an actionable message.

**Data flow**: It receives a PipedreamError and provider name. It keeps the original status code, appends stale grant guidance for that provider to the error body, and returns a new PipedreamError. The original service failure becomes easier for the agent or user to respond to.

**Call relations**: PipedreamBroker.execute uses this when a run appears to involve a stale account, and PipedreamBroker.credential uses it when an account lookup returns not found. It delegates the wording of the reconnect advice to stale_grant_guidance.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 209–213)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the Pipedream connector specification for a UFO provider name. The specification tells the broker which Pipedream app slug belongs to that provider.

**Data flow**: It receives a provider string. It reads the registered Pipedream connector map, returns the matching ConnectorSpec if present, and raises KeyError if the provider is not registered. Nothing else is changed.

**Call relations**: This is the common provider-to-Pipedream translation step. tools, execute, credential, and _key_miss all call it before talking to Pipedream about a particular app.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 216–232)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Turns raw Pipedream catalog rows into UFO BrokerTool objects. It filters out unusable rows and gives each valid action a slug, description, and input schema.

**Data flow**: It receives a tuple of dictionaries from Pipedream's action listing. For each row with a non-empty string key, it extracts the description, reads configurable properties, builds an input schema, and appends a BrokerTool. The output is a tuple of clean tool descriptions.

**Call relations**: PipedreamBroker.tools uses this for normal discovery, and _key_miss uses it to list valid action names in an error message. It calls _props, _input_schema, and _str to clean up Pipedream's raw fields.

*Call graph*: calls 3 internal fn (_input_schema, _props, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 235–237)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable property list from a Pipedream action definition or listing row. These properties are the fields that can become user inputs or internal account slots.

**Data flow**: It receives a dictionary that may contain configurable_props. If that value is a list, it keeps only the entries that are dictionaries; otherwise it returns an empty list. The output is a clean list of property dictionaries.

**Call relations**: PipedreamBroker.schema and _listed_tools use this before building input schemas. _app_slot uses it to search the same properties for the hidden connected-account field.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 240–247)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream property where the broker must insert the connected account. Without this slot, an action cannot run on behalf of the member's granted account.

**Data flow**: It receives an action definition and slug. It scans the configurable properties for one whose type is Pipedream's app property type and whose name is a non-empty string. It returns that property name, or raises a PipedreamError if no usable slot exists.

**Call relations**: PipedreamBroker.execute calls this just before running an action. The returned name becomes the key where execute inserts the account's authProvisionId.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 250–273)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the agent-facing JSON schema for an action's inputs. It hides fields that belong to Pipedream itself, such as the account slot and internal service fields.

**Data flow**: It receives a list of property dictionaries. It skips invalid names, internal property types, and service properties beginning with '$.', maps Pipedream property types to JSON types, adds descriptions when available, and marks non-optional fields as required. The output is a JSON-schema-like dictionary with type, properties, and sometimes required.

**Call relations**: PipedreamBroker.schema uses this for one action's detailed schema, and _listed_tools uses it while presenting catalog search results. It calls _str to safely normalize property type values.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 276–277)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only if it already is one. This prevents accidental values like numbers or dictionaries from leaking into descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It has no side effects.

**Call relations**: PipedreamBroker.schema, _listed_tools, and _input_schema use this small helper while cleaning Pipedream metadata. It keeps schema and description building simple and defensive.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the `ufo_ext_pipedream` folder should be treated as an importable package. Without it, depending on the Python version and packaging setup, other parts of the project might not be able to import modules from this extension in the expected way.

Think of it like a label on a folder in a filing cabinet. The label does not contain the documents, but it tells the system, “this folder belongs together and can be looked up by name.” In this case, the folder appears to belong to a Pipedream extension, but this particular file does not define any Pipedream behavior, settings, classes, or helper functions.

Because it is empty, importing this package has no side effects: it does not open files, contact services, register handlers, or change global state. Its value is in making the package layout clear and compatible with Python tooling.


### Hosted account connection
Pipedream Connect client and provider code create consent flows, verify accounts, discover actions, and execute them through Pipedream.

### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `request handling and connector execution`

This file solves a security-sensitive broker problem: users need to connect outside services, but the project should not store or handle their real OAuth tokens. OAuth is the common web sign-in-and-permission flow, and here Pipedream keeps the provider token on its side. This code stores and passes around only Pipedream connected-account IDs.

The main class, PipedreamClient, is an async client, meaning it talks to the network without blocking the whole program while waiting. Before each Pipedream API call, it gets a short-lived project access token using the deploy's Pipedream client ID, secret, and project ID from environment variables. It caches that token until it is close to expiring.

The file also acts like a careful doorman. A Pipedream project token can read many connected accounts, so methods such as connected_account, newest_account, and workspace_account verify that the account really belongs to the expected user or workspace before allowing it to be used. Without those checks, one user or workspace could accidentally be allowed to act through another user's account.

For actions, the client can list Pipedream's available components, fetch one action's definition, and run it with configured inputs. It also asks Pipedream to create a fresh file stash for each run, so files produced inside Pipedream can come back as downloadable links rather than unusable temporary paths.

#### Function details

##### `PipedreamError.__init__`  (lines 80–83)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception when Pipedream fails or returns data this client cannot safely use. It keeps both the status code and response body so callers can explain or log what went wrong.

**Data flow**: It receives a numeric status and a text body. It builds a readable error message, stores the status and body on the error object, and returns an exception ready to be raised.

**Call relations**: Many parts of this client and the broker raise this error when they cannot continue safely, such as missing access tokens, account ownership mismatches, unhealthy accounts, or failed Pipedream responses.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 121–142)

```
async def access_token(self) -> str
```

**Purpose**: Gets the project-level access token needed to call Pipedream's Connect API. It reuses a cached token when possible so the system does not ask Pipedream for a new one on every request.

**Data flow**: It reads the client ID from the object and checks the shared token cache. If the cached token is still safely valid, it returns it. Otherwise it opens an HTTP client, sends the client ID and secret to Pipedream's OAuth token endpoint, checks the response, stores the new token with its expiry time, and returns the token text.

**Call relations**: _get and _post call this before making authenticated API requests. It uses _http to create the network client and _body to turn the HTTP response into a usable dictionary, raising PipedreamError if Pipedream did not provide a valid token.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 144–159)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted consent link for a user. This is the link the user's browser opens so they can grant access to a provider account.

**Data flow**: It receives an external user ID plus success and error redirect URLs. It posts those values to Pipedream, checks that the reply contains both a token and a connect-link URL, and returns them packaged as a ConnectToken.

**Call relations**: Higher-level OAuth or connector setup code uses this when starting the account-connection flow. It delegates the actual HTTP call to _post and raises PipedreamError if Pipedream's answer is missing the required link or token.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 161–168)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Fetches one connected account and proves it belongs to the expected external user. This protects against using a powerful project token to accidentally access someone else's account.

**Data flow**: It receives a Pipedream account ID and an expected external user ID. It fetches the account record from Pipedream, unwraps the data if needed, checks ownership and health, and returns a ConnectedAccount summary.

**Call relations**: This is used after code already has an account ID but must verify it before binding or reading anything further. It calls _get for the API read, _dict to safely handle response shape, and _owned_account for the ownership check.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 170–174)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Reads the friendly display name for a connected account, if Pipedream has one. This is useful for showing a human-readable account label in the UI.

**Data flow**: It receives an account ID, fetches the account record, looks for a non-empty string in the account's name field, and returns that string or None if no usable name is present.

**Call relations**: Caller code can use this for display only. It relies on _get to retrieve the account and _dict to avoid crashing if Pipedream wraps or shapes the response unexpectedly.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 176–186)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Fetches a connected account and proves it belongs to the given workspace. A workspace is a tenant or team boundary, so this stops one workspace from using another workspace's connection.

**Data flow**: It receives an account ID and workspace UUID. It fetches the account, turns the response into a ConnectedAccount, checks whether the account's external user ID matches the workspace's allowed naming pattern, and either returns the account or raises an access error.

**Call relations**: Connector execution paths use this before running actions with a stored account ID. It combines _get, _dict, _account, and _workspace_owns_external_user, raising PipedreamError when the account does not belong to the workspace.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 188–202)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for a specific external user and app. This is used after a consent flow finishes, when the system needs to identify the account that was just connected.

**Data flow**: It receives an external user ID and app slug, asks Pipedream for matching accounts, filters the response to dictionary-like records, chooses the one with the latest created_at value, checks it has an ID, verifies ownership, and returns a ConnectedAccount.

**Call relations**: OAuth callback or exchange code can call this to connect the browser-return event to the new Pipedream account. It uses _get for the listing and _owned_account to make sure the newest account is still owned by the expected user.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 204–233)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the Pipedream actions available for one app, optionally filtered by a search query. It follows pages of results so discovery is not limited to only the first slice of Pipedream's catalog.

**Data flow**: It receives an app slug and optional query. It repeatedly requests action pages, adds valid action dictionaries to a list, follows Pipedream's cursor to the next page, and stops when the page is short, the cursor is missing, or the safety limit is reached. It returns the collected actions as a tuple.

**Call relations**: The broker calls this when it cannot find a requested action key and wants to search or suggest catalog entries. It uses _get for each page and _dict to read pagination information safely.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 235–236)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition of one Pipedream action component. This tells the rest of the system what inputs the action expects and how it is described.

**Data flow**: It receives an action key, requests that component from Pipedream, and returns the response dictionary.

**Call relations**: Higher-level tool-building code can call this when it needs details for one chosen action. It is a thin wrapper around _get.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 238–256)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on the server side using a connected user's account. It also asks Pipedream to create a fresh file stash so files produced by the action can be returned as usable download links.

**Data flow**: It receives an action key, an external user ID, and configured input properties. It builds the run request, checks that the JSON payload is not larger than the allowed size, posts it to Pipedream, and returns Pipedream's result dictionary.

**Call relations**: Connector execution code uses this after it has chosen an action and prepared its inputs. It delegates the network request to _post and prevents oversized argument payloads before they leave the process.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 258–261)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated HTTP GET request to Pipedream and returns a checked dictionary response. It is the shared read path for the client.

**Data flow**: It receives an API path and optional query parameters. It first gets an access token, opens an HTTP client with that token, sends the GET request, passes the response through _body, and returns the parsed dictionary.

**Call relations**: Account lookup, action listing, and action definition methods all call this instead of repeating authentication and response-checking code. It calls access_token, _http, and _body.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 6 (account_label, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 263–266)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated HTTP POST request to Pipedream and returns a checked dictionary response. It is the shared write-or-run path for the client.

**Data flow**: It receives an API path and a JSON-like body dictionary. It gets an access token, opens an authenticated HTTP client, posts the body as JSON, validates and parses the response through _body, and returns the resulting dictionary.

**Call relations**: connect_token and run_action call this when they need to create something or execute an action. It centralizes the token, HTTP client, and response validation steps.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 268–281)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates a short-lived HTTP client configured for Pipedream. For authenticated calls, it adds the bearer token and environment header; for the token request, it leaves headers empty.

**Data flow**: It receives an optional access token. It builds headers if a token is present, then returns an httpx AsyncClient with Pipedream's base URL, timeout, optional test transport, and those headers.

**Call relations**: access_token uses this without a token to ask for credentials. _get and _post use it with a token to make authenticated Pipedream Connect calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 284–285)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This prevents response-shape surprises from causing ordinary attribute errors.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Several client methods use this when reading nested Pipedream response fields. _account also uses it to inspect the nested app object without assuming Pipedream sent the expected shape.

*Call graph*: called by 6 (account_label, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 288–301)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that an account record belongs to the exact external user the caller expected. This is a core safety check against cross-user account use.

**Data flow**: It receives an account record, account ID, and expected external user ID. It first converts the record into a ConnectedAccount using _account, then compares the account's owner with the expected owner. If they match, it returns the account; if not, it raises PipedreamError.

**Call relations**: connected_account and newest_account call this whenever they need account information scoped to one external user. It builds on _account's basic validation and adds the ownership rule.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 304–316)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into the smaller ConnectedAccount object this project uses. It also rejects records that lack an owner or are marked unhealthy.

**Data flow**: It receives a raw account dictionary and the account ID being read. It checks for a non-empty external owner, rejects an explicitly unhealthy account, extracts the app slug if present, and returns a ConnectedAccount with the ID, app, and owner.

**Call relations**: workspace_account uses this before checking workspace ownership, and _owned_account uses it before checking exact user ownership. It raises PipedreamError when the Pipedream record is not safe to use.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 319–320)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used in Pipedream external user IDs for a workspace. This makes all connection-specific user IDs easy to recognize as belonging to that workspace.

**Data flow**: It receives a workspace UUID. It formats the fixed project prefix, the workspace ID in compact hexadecimal form, and a trailing underscore, then returns that string.

**Call relations**: _workspace_owns_external_user uses this to test account ownership, and connection_user_id uses it when creating a new external user ID for a connection flow.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 323–332)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user ID belongs to a workspace. It accepts both an older direct workspace format and the newer workspace-plus-connection format.

**Data flow**: It receives a workspace UUID and an external user ID string. It first checks the older exact format. If that does not match, it checks for the workspace prefix, removes it, and verifies that the remaining connection ID is exactly 32 lowercase hexadecimal characters. It returns True or False.

**Call relations**: workspace_account calls this after reading an account to decide whether the workspace is allowed to use it. It uses workspace_user_prefix to keep the ownership format consistent.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 335–337)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user ID for one workspace connection attempt. It uses the connection state to make a deterministic, non-secret-looking connection identifier.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first 32 hexadecimal characters, attaches them to the workspace prefix, and returns the full external user ID.

**Call relations**: OAuth setup code can use this when starting a consent flow so the account created by Pipedream can later be tied back to the workspace and that specific flow. It shares the same prefix format checked by _workspace_owns_external_user.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 340–348)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe dictionary or raises a clear error. It is the common response gatekeeper for all network calls in this file.

**Data flow**: It receives an httpx Response. If the status code is an error, it raises PipedreamError with the response text. If the response is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if the JSON is an object; non-object JSON is rejected.

**Call relations**: access_token, _get, and _post all pass responses through this before using them. This means the rest of the client can work with dictionaries instead of checking HTTP status and JSON shape every time.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 351–369)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a PipedreamClient from environment variables for the current deployment. It fails early if the required Pipedream credentials or project ID are missing.

**Data flow**: It reads the client ID, client secret, project ID, and optional environment name from process environment variables. If any required value is missing, it raises RuntimeError. Otherwise it returns a configured PipedreamClient.

**Call relations**: Higher-level broker or route code can call this when it needs the real deploy's Pipedream client. It is the bridge between deployment configuration and the PipedreamClient object used for API calls.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during connector authorization`

ufo needs a normal-looking OAuth flow: send the user to an authorization URL, get them back with a code, then exchange that code for an account. Pipedream works a little differently: before the user can visit Pipedream's Connect Link, ufo must first make an asynchronous API call to create a short-lived connect token. This file solves that mismatch.

The main idea is to send the browser to ufo's own bridge route first, instead of directly to Pipedream. That route creates the Pipedream token, builds the Pipedream consent link, and redirects the browser there. When Pipedream sends the browser back, the same route finds the newly connected account and redirects back into ufo's core flow with that account id.

A key safety detail is that each connect attempt gets its own Pipedream “external user” id, built from the workspace and sealed state. That is like giving each checkout customer their own numbered basket: when the user returns, the system looks only in that basket, so overlapping sign-in attempts cannot accidentally pick up someone else's account. The final exchange step checks again that the account belongs to the expected Pipedream app before ufo binds it as an OAuth account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL that ufo gives to the user's browser when starting a Pipedream-backed connection. Instead of pointing straight at Pipedream, it points at this extension's own OAuth bridge route so the async Pipedream setup can happen there.

**Data flow**: It receives the sealed connection state and the callback URL that ufo core expects the browser to return to. It extracts the origin, meaning the scheme and host such as `https://example.com`, from the callback URL, adds the provider, state, and callback as query parameters, and returns a complete bridge URL.

**Call relations**: This is the first step in the flow described by the provider. It calls `_origin` to make sure the callback URL has a usable web origin, then hands the browser off to `oauth_route`, which does the real Pipedream token creation and redirect work.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–68)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This finishes the connection from ufo's point of view. Given an account id returned through the bridge, it asks Pipedream for that exact connected account and turns it into ufo's standard `OAuthAccount` record.

**Data flow**: It receives a code, which in this flow is really the Pipedream connected account id, plus the workspace id and sealed state. It rebuilds the state-specific external user id, asks the Pipedream client for that account, checks that the account belongs to the expected app, optionally fetches a human-friendly account label, and returns an `OAuthAccount` containing the account id and label. If the account belongs to the wrong app, it raises a Pipedream error instead of binding it.

**Call relations**: This runs after `oauth_route` has redirected back to ufo core with an account id. It relies on Pipedream client helpers to identify the right external user and fetch the connected account, then hands ufo core a clean account object it can store as the completed grant.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 71–114)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge route for both halves of Pipedream consent. On the way out, it creates a Pipedream Connect token and redirects the user to Pipedream; on the way back, it finds the connected account and redirects back to ufo core.

**Data flow**: It reads query parameters from the incoming HTTP request: the provider, sealed state, callback URL, and optional outcome marker. If required state or callback information is missing, it returns a clear error response. If the provider is unknown, it returns a not-found response. If Pipedream reports a successful connection, it looks up the newest account for this exact state-specific external user and redirects to the callback with the state and account id. If Pipedream reports failure, it returns an error telling the user to try connecting again. If there is no outcome yet, it creates a Pipedream connect token, builds a hosted Connect Link for the provider's app, optionally adds a custom OAuth app id from the environment, and redirects the browser to Pipedream.

**Call relations**: This route is reached first by the URL made in `PipedreamOAuthProvider.authorize_url`. It uses `_origin` to build safe return URLs, Pipedream client calls to create tokens and find accounts, and HTTP responses to move the browser through the flow. On success, it sends control back to ufo core, which then calls `PipedreamOAuthProvider.exchange` to verify and bind the account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 117–121)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the base web origin from a URL, such as `https://example.com`. It also protects the OAuth bridge from malformed callback URLs that do not include a proper scheme and host.

**Data flow**: It receives a URL string, parses it, checks that it uses `http` or `https` and has a host name, then returns only the scheme and host. If the URL cannot be used as a browser origin, it raises an error instead of building a bad redirect.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` call this helper when they need to build bridge URLs from callback URLs. It is the shared guardrail that keeps the redirect addresses well formed before the browser is sent onward.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Token-safe request proxy
Proxy transport rewrites provider HTTP calls through Pipedream Connect Proxy so provider tokens stay hidden from UFO.

### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling`

Some connected accounts are owned by Pipedream, not by this application. That means a feed-sync connector cannot simply keep a Gmail, Slack, or other provider token and call the provider itself. This file solves that by acting like a forwarding tunnel. A connector can still make an ordinary HTTP request to the provider URL, but this transport quietly repackages it as a request to Pipedream's proxy endpoint. Pipedream then adds the real credential on its own servers and forwards the call upstream.

The important detail is that the provider's response comes back mostly unchanged: status code, body, and headers are preserved. That matters because connectors often make decisions from those details, such as treating a 404 as an expired cursor or a 401 as a skipped stream.

The transport also rewrites request headers carefully. Pipedream only forwards headers that start with `x-pd-proxy-`, so this file adds that prefix to useful headers and drops low-level transport headers such as `host`, `content-length`, and `authorization`. In everyday terms, it is like putting a letter inside a Pipedream-approved envelope: the message still reaches the same destination, but Pipedream is the trusted courier that adds the secret key.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main forwarding step. It takes a normal HTTP request meant for a provider, turns it into a Pipedream Connect Proxy request, and sends it through the underlying HTTP transport so Pipedream can inject the account credential.

**Data flow**: It receives an `httpx.Request`, which contains the original method, URL, headers, and body. It asks the Pipedream client for an access token, reads the request body, copies safe headers while adding Pipedream's required proxy prefix, encodes the original provider URL into the proxy path, and builds a new request to Pipedream with the account and external user in the query string. The result is the response returned by the inner transport, which should represent the provider's response passed back through Pipedream.

**Call relations**: When an `httpx.AsyncClient` uses this transport, it calls this method for each outgoing request. Inside that flow, the method uses `request.aread` to capture the original body, `base64.urlsafe_b64encode` to make the provider URL safe to place in the proxy path, and `httpx.URL` plus `httpx.Request` to create the new Pipedream request. It then hands the rewritten request to `inner.handle_async_request`, which performs the actual network send.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport when the proxy transport is no longer needed. It prevents open network resources from being left behind.

**Data flow**: It receives no extra input beyond the transport object itself. It calls the inner transport's async close method, letting that lower-level transport release any connections or resources it owns. It returns nothing after cleanup is complete.

**Call relations**: This is called during client shutdown or cleanup, following the normal `httpx` transport lifecycle. Rather than doing its own cleanup, it passes the close request straight through to the wrapped inner transport because that is the part that owns the actual network connections.

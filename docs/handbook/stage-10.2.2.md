# Pipedream Connector Broker and Proxy  `stage-10.2.2`

This stage is shared behind-the-scenes support for connecting UFO to outside apps through Pipedream. Pipedream acts like a trusted middle desk: users approve access on Pipedream’s hosted pages, and UFO can use that access without storing private tokens itself.

The package marker file only makes this extension importable. The broker is the main control point. It translates Pipedream actions into tools the rest of UFO can discover, shows what inputs they need, runs them for the right connected account, and makes any output files available afterward. The client is the low-level messenger to Pipedream Connect. It creates consent links, verifies which user owns a connected account, lists available actions, and sends action-run requests. The provider adapts UFO’s normal “connect an account” flow to Pipedream’s redirect-based approval process. The proxy supports direct-style calls to outside services: UFO sends a request to Pipedream, Pipedream adds the secret token, and UFO receives the response as if it had called the service itself.

## Files in this stage

### Package and Broker Entry Point
Defines the importable Pipedream extension package and the main broker that exposes Pipedream actions to UFO's connector system.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the folder should be treated as an importable package, much like putting a label on a drawer so other code can find what is inside. For this extension, the drawer is `ufo_ext_pipedream`, which likely contains code related to the Pipedream integration elsewhere in the same directory. Because the file is empty, importing the package does not run setup code, define shortcuts, or expose any names directly. Its main value is structural: without it, some tools or Python versions may not recognize the folder as a normal package, and imports that expect `ufo_ext_pipedream` to exist could fail.


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Pipedream is an external service that hosts many ready-made actions, such as sending an email or creating a record in another app. This file wraps those actions in the shape UFO expects from a connector broker: something that can list tools, explain a tool's inputs, run a tool, and provide credentials for network access.

The broker is deliberately stateless. Instead of keeping a long-lived Pipedream client inside itself, each method asks for the current client when it runs. That matters for tests and for safety: if a test swaps the network transport, or if credentials change, each call sees the latest setup.

A key job here is translation. Pipedream describes actions using its own fields, such as `configurable_props`. The broker turns those into `BrokerTool` objects and simple JSON schemas, which are plain descriptions of what inputs a caller may provide. It hides internal Pipedream fields, especially the connected-account slot, because the broker fills that in itself when executing.

When running an action, the broker checks that the chosen account really belongs to the requested app, binds that account into the action, then calls Pipedream's server-side run API. It also turns common failure cases into more helpful messages, such as suggesting nearby action names or telling the user to reconnect an old account. File outputs are read from Pipedream's File Stash upload list and returned as downloadable URLs.

#### Function details

##### `PipedreamBroker.tools`  (lines 62–64)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for a given provider and search query, then presents them as UFO broker tools. This is used when the system wants to discover what actions are available for an app.

**Data flow**: It receives a workspace ID, provider name, and search text. It looks up the provider's Pipedream app slug, asks the current Pipedream client for matching actions, converts the returned action rows into broker-friendly tool objects, and returns them as a tuple.

**Call relations**: This is the discovery step. `PipedreamBroker.search` calls it when it needs search results, and it hands raw Pipedream action listings to `_listed_tools` so the rest of the system sees a consistent tool format.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 66–73)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the detailed description of one Pipedream action, including the inputs a caller is allowed to provide. It is used when the system needs to know how to call a specific action safely.

**Data flow**: It receives a workspace ID, provider name, and action slug. It fetches the action definition, extracts its configurable input fields, filters and converts those fields into a JSON schema, reads its description and read-only hint, and returns a `BrokerTool` object.

**Call relations**: This follows after a tool has been selected. It relies on `_definition` to fetch Pipedream's action data, then uses `_props`, `_input_schema`, `_read_only`, and `_str` to turn that data into the broker format expected by callers.

*Call graph*: calls 5 internal fn (_definition, _input_schema, _props, _read_only, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 75–110)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a chosen Pipedream action using a specific connected account. It also checks account ownership, fills in the hidden account field, and turns several confusing Pipedream failures into clearer errors.

**Data flow**: It receives the workspace, provider, action slug, caller-provided arguments, account ID, and an optional idempotency key. It fetches the action definition, adds the account binding into the correct app slot, verifies that the account belongs to the requested provider, sends the configured action to Pipedream, checks for action-level errors in the response, and returns the successful response dictionary. If the action key is unknown, the account is stale, or the wrong app is connected, it raises an explanatory error instead.

**Call relations**: This is the main run path. It calls `_definition` to learn how the action is shaped, `_app_slot` to find where the account must be inserted, `_spec` to verify the provider, `_key_miss` to suggest better action names, and `_stale_account` plus `_reconnect_error` to guide users when an old grant no longer works.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 112–130)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Pipedream action and turns them into broker file records with names and download URLs. This lets the sandbox fetch files that the remote action saved.

**Data flow**: It receives the action response dictionary. It looks inside the response's exported File Stash upload list, ignores malformed entries, takes each valid download URL, derives a filename from the action's local file path when available, and returns a tuple of `BrokerFile` objects.

**Call relations**: This runs after `execute` returns a response. It does not call back into Pipedream; it simply reads the File Stash metadata already present in the response and packages it for the rest of UFO.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 132–144)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged uploads for Pipedream actions because Pipedream expects file inputs as URLs instead. It points callers toward sharing the workspace file and passing the download link.

**Data flow**: It receives details about a file the caller wants to upload, such as filename, MIME type, and checksum. Instead of creating an upload target, it immediately raises a `ValueError` explaining that this connector does not use staged uploads.

**Call relations**: This is a guardrail in the broker interface. If a generic connector flow tries to stage a file for Pipedream, this method stops it and explains the correct Pipedream-specific path.


##### `PipedreamBroker.search`  (lines 146–147)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for available Pipedream actions and wraps the results in a `BrokerSearch` object. Pipedream has no separate planning router here, so the result is essentially just the matching tools.

**Data flow**: It receives a workspace ID, provider, and query. It asks `PipedreamBroker.tools` for matching tool descriptions and places those tools into a search result object.

**Call relations**: This is a thin search wrapper around `tools`. It is used when the broader connector system asks for a catalog search rather than a bare tool list.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 149–170)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Builds a credential object that lets UFO make proxied network calls through a connected Pipedream account. It first proves that the account exists in this workspace and belongs to the requested app.

**Data flow**: It receives a workspace ID, provider name, and account ID. It looks up the provider's expected Pipedream app, fetches the workspace account, turns a missing account into reconnect guidance, rejects accounts connected to the wrong app, then returns a `Credential` containing a `PipedreamProxyTransport` for authenticated proxy access.

**Call relations**: This is used when the system needs direct authenticated transport rather than running a predefined action. It relies on `_spec` for the provider mapping, the Pipedream client for account lookup, and `PipedreamProxyTransport` to carry future requests through the right account.

*Call graph*: calls 3 internal fn (__init__, _spec, __init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, pipedream_client).


##### `PipedreamBroker._definition`  (lines 172–180)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for one action slug. It turns a missing action into UFO's standard “unknown tool” signal.

**Data flow**: It receives an action slug. It asks the current Pipedream client for that action's definition, treats a 404 response as an unknown broker tool, then returns the nested `data` dictionary if Pipedream wrapped the definition that way, or the whole payload otherwise.

**Call relations**: `PipedreamBroker.schema` uses this to describe a tool, and `PipedreamBroker.execute` uses it to know how to bind the connected account and validate inputs. It is the shared fetch point for detailed action metadata.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 182–201)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Creates a helpful error when someone tries to run an action slug that Pipedream does not know. Instead of only saying “not found,” it may suggest similar real action keys.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider's app, tries to list that app's actions, compares the missing slug with real slugs, and returns a `PipedreamError` that includes either close matches or advice to search the app's actions.

**Call relations**: `PipedreamBroker.execute` calls this after `_definition` reports an unknown tool. It uses `_listed_tools` to normalize catalog rows before comparing names, so the hint is based on the same tool keys users would see in discovery.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute); 1 external calls (get_close_matches).


##### `_stale_account`  (lines 204–211)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream error probably means the connected account is no longer usable. This helps the broker tell the user to reconnect only when the failure matches a narrow stale-account pattern.

**Data flow**: It receives a `PipedreamError` and the account ID being used. It lowercases the error body and checks for Pipedream's “external user not found” wording, or for the specific account ID together with “not found.” It returns `true` when the error looks like a stale grant, otherwise `false`.

**Call relations**: `PipedreamBroker.execute` uses this when Pipedream rejects a run or when the action response contains an error. If it returns true, execution routes the error through `_reconnect_error` to add user-facing reconnect instructions.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 214–215)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds reconnect guidance to a Pipedream error. It keeps the original status and message, but appends advice for fixing an unusable grant.

**Data flow**: It receives the original Pipedream error and provider name. It asks the connector system for stale-grant guidance for that provider, appends it to the error body, and returns a new `PipedreamError` with the same status code.

**Call relations**: `PipedreamBroker.execute` calls this after `_stale_account` identifies an old or missing account. It turns a low-level service failure into a message an agent or user can act on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 218–222)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the Pipedream connector specification for a UFO provider name. This is how the broker translates from UFO's provider label to Pipedream's app slug.

**Data flow**: It receives a provider string. It checks the registered Pipedream connector map and returns the matching `ConnectorSpec`; if no provider is registered, it raises a `KeyError`.

**Call relations**: Several broker paths use this whenever they need the real Pipedream app identity: discovery in `tools`, execution in `execute`, credential checks in `credential`, and missing-key suggestions in `_key_miss`.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 225–242)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream action listing rows into UFO `BrokerTool` objects. It makes catalog search results immediately usable by including each action's key, description, input schema, and read-only hint.

**Data flow**: It receives a tuple of raw Pipedream action dictionaries. For each row with a valid action key, it extracts a safe description, builds an input schema from configurable properties, detects whether the action is read-only, and returns all valid tools as a tuple.

**Call relations**: `PipedreamBroker.tools` uses this for normal discovery, and `_key_miss` uses it when building suggestions. It delegates field cleanup to `_props`, `_input_schema`, `_read_only`, and `_str`.

*Call graph*: calls 4 internal fn (_input_schema, _props, _read_only, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_read_only`  (lines 245–247)

```
def _read_only(definition: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Pipedream action is marked as read-only, meaning it is intended not to change external state. This gives callers a safety hint about what the action may do.

**Data flow**: It receives an action definition dictionary. It looks for an `annotations` dictionary and returns true only when `readOnlyHint` is exactly true; otherwise it returns false.

**Call relations**: Both `PipedreamBroker.schema` and `_listed_tools` call this while building `BrokerTool` objects. It provides one small piece of safety metadata for tool descriptions.

*Call graph*: called by 2 (schema, _listed_tools).


##### `_props`  (lines 250–252)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the usable list of configurable properties from a Pipedream action definition. It filters out malformed entries so later code can work with clean dictionaries.

**Data flow**: It receives an action definition dictionary. It reads the `configurable_props` field, keeps only items that are themselves dictionaries, and returns that cleaned list; if the field is missing or not a list, it returns an empty list.

**Call relations**: `PipedreamBroker.schema`, `_listed_tools`, and `_app_slot` all use this before inspecting action inputs. It is the shared cleanup step for Pipedream's property data.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 255–262)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special input field where Pipedream expects the connected app account to be placed. Without this slot, the broker cannot run the action on behalf of a user's granted account.

**Data flow**: It receives an action definition and slug. It scans the cleaned configurable properties for a property whose type is Pipedream's app-account type and whose name is a non-empty string. It returns that property name, or raises a `PipedreamError` if no such slot exists.

**Call relations**: `PipedreamBroker.execute` calls this just before running an action. The returned slot name tells execution where to insert the account's `authProvisionId`.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 265–288)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds a simple JSON schema describing the action inputs that the caller may provide. It hides Pipedream's internal fields and the connected-account field because those are not meant to be filled in by the model or user.

**Data flow**: It receives a cleaned list of Pipedream property dictionaries. It skips properties without names, skips internal property types such as app-account fields, service fields beginning with `$.`, and directory fields, converts known Pipedream types into JSON schema types, adds descriptions from labels or descriptions, records required fields when they are not optional, and returns an object-shaped schema.

**Call relations**: `PipedreamBroker.schema` uses this for a single detailed tool description, and `_listed_tools` uses it during catalog listing. It calls `_str` to safely read property type strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 291–292)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. It avoids accidentally treating non-string Pipedream data as meaningful text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `PipedreamBroker.schema`, `_listed_tools`, and `_input_schema` use this as a small cleanup helper when reading descriptions and property types from external Pipedream data.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### Pipedream Connect Flow
Handles hosted consent, connected account verification, action discovery, and safe action execution through Pipedream Connect.

### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector consent, account verification, and connector action execution`

This file is the bridge between UFO and Pipedream Connect. Pipedream acts like a trusted clerk: it asks the user for permission to use Gmail, Linear, Discord, and other apps, stores the real OAuth token itself, and gives UFO only a connected-account id. That matters because UFO can call tools on the user’s behalf without keeping the user’s secrets.

The file also defines which providers this Pipedream extension is allowed to broker. The list is intentionally small: these are apps where the project cannot rely on the other connector broker, or where Pipedream offers the needed managed consent or actions.

The central class, PipedreamClient, talks to Pipedream’s REST API over HTTP. Before most calls, it gets a short-lived Pipedream access token using the deployment’s client id and secret, then caches it so every request does not need a fresh login. It can create a hosted connect link, read connected accounts, find the newest account after a user finishes consent, list Pipedream’s action catalog, fetch one action’s definition, and run an action server-side.

A major safety rule in this file is ownership checking. The project-level Pipedream token can read accounts across the project, so the code verifies that an account belongs to the expected user or workspace before allowing it to be used. Without that guard, one workspace could accidentally act through another workspace’s connected account.

#### Function details

##### `PipedreamError.__init__`  (lines 105–108)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error when Pipedream rejects a request or replies in a form this client cannot safely use. It keeps both the HTTP status code and the response text so callers can explain or log what went wrong.

**Data flow**: It receives a status number and response body text → builds a message like “pipedream 403: ...” → stores the status and body on the error object for later inspection.

**Call relations**: Many parts of this file and the Pipedream broker raise this error when they cannot continue safely. It is the common way failed API calls, missing fields, ownership mismatches, and unusable responses are reported upward.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 146–167)

```
async def access_token(self) -> str
```

**Purpose**: Gets the deployment’s access token for calling Pipedream’s API. It reuses a cached token until it is close to expiring, which avoids unnecessary login requests.

**Data flow**: It reads the client id, client secret, and the process-wide token cache → if a still-good token exists, it returns it → otherwise it posts a client-credentials request to Pipedream, checks the response, stores the new token with its expiry time, and returns the token string.

**Call relations**: The private request helpers, PipedreamClient._get and PipedreamClient._post, call this before making authenticated API calls. It uses PipedreamClient._http to open the HTTP client and _body to turn the HTTP response into a checked dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 169–184)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted link that a user’s browser can open to approve access to an outside app. This is the start of the consent flow.

**Data flow**: It receives the external user id plus success and error redirect URLs → sends them to Pipedream → expects back a token and connect-link URL → returns them as a ConnectToken object, or raises an error if either piece is missing.

**Call relations**: Higher-level broker code uses this when it needs to send a user to Pipedream’s hosted consent page. It hands the actual API call to PipedreamClient._post and uses PipedreamError if Pipedream’s reply is not usable.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 186–193)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and confirms it belongs to the expected external user. This prevents the system from using an account that was connected by someone else.

**Data flow**: It receives a Pipedream account id and expected external user id → fetches the account record from Pipedream → unwraps the data field if present → verifies ownership and health → returns a ConnectedAccount summary.

**Call relations**: This is part of the safety check before a grant is trusted. It calls PipedreamClient._get for the API read, _dict for safe response unwrapping, and _owned_account for the ownership check.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 195–199)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up the human-readable name of a connected account, if Pipedream has one. This can be used for display, such as showing which account a user connected.

**Data flow**: It receives an account id → fetches the account record from Pipedream → looks for a non-empty name field → returns that name, or null if there is no usable label.

**Call relations**: It is a lightweight account read built on PipedreamClient._get. Unlike connected_account or workspace_account, it does not prove ownership; it only extracts a display label.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 201–211)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads a connected account and confirms it belongs to the given workspace. This is another confused-deputy guard: it makes sure a powerful project token is not used to act for the wrong workspace.

**Data flow**: It receives an account id and workspace id → fetches the account record → converts it into a ConnectedAccount → checks whether the account’s external user id matches the workspace’s allowed pattern → returns the account if allowed, or raises an error if not.

**Call relations**: Broker execution code can use this before running actions for a workspace. It relies on PipedreamClient._get for the read, _account for account validation, and _workspace_owns_external_user for the workspace ownership rule.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 213–227)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for a specific external user and app. This is useful immediately after a user returns from Pipedream’s consent page, when the system needs to identify what account was just connected.

**Data flow**: It receives an external user id and app slug → asks Pipedream for matching accounts → filters the response to dictionary records → chooses the record with the latest created_at value → checks that it has an id and belongs to the expected user → returns a ConnectedAccount.

**Call relations**: This completes the consent flow after Pipedream redirects back. It calls PipedreamClient._get to list accounts, then _owned_account to make sure the returned account really belongs to the user who started the flow.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 229–258)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the Pipedream actions available for an app, optionally filtered by a search query. It follows pages of results so discovery does not miss actions that are not on the first page.

**Data flow**: It receives an app slug and optional search text → repeatedly requests pages of actions from Pipedream → collects dictionary-like action rows → stops when the page is short, there is no next cursor, or the configured maximum is reached → returns the collected rows as a tuple.

**Call relations**: The broker calls this when it needs to discover or explain available actions, especially after a requested action key is not immediately known. It uses PipedreamClient._get for each page and _dict to read the paging cursor safely.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 260–261)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition of one Pipedream action component. Callers use this to learn what inputs an action expects before running it.

**Data flow**: It receives an action key → requests that component from Pipedream → returns the response dictionary.

**Call relations**: This is a direct wrapper around PipedreamClient._get. It sits between higher-level tool-description code and Pipedream’s component-definition endpoint.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 263–281)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on the server side for a connected user. It also asks Pipedream to use a fresh file stash so files created by the action can come back as downloadable links instead of unreachable temporary paths.

**Data flow**: It receives an action key, external user id, and configured action inputs → builds the run request body → checks that the request is not larger than the allowed payload size → posts it to Pipedream → returns Pipedream’s run result dictionary.

**Call relations**: Higher-level connector execution code uses this after it has chosen an action and prepared its inputs. It hands the HTTP work to PipedreamClient._post and depends on Pipedream to bind the stored account credentials server-side.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 283–286)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked JSON object. It is the shared path for reading data from Pipedream.

**Data flow**: It receives an API path and optional query parameters → obtains an access token → opens an HTTP client with that token → sends the GET request → passes the response through _body → returns the parsed dictionary.

**Call relations**: Account lookup, action listing, and action-definition methods all call this helper. It centralizes the repeated steps of getting a token, opening the HTTP client, and validating the response.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 6 (account_label, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 288–291)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked JSON object. It is the shared path for creating tokens and running actions.

**Data flow**: It receives an API path and JSON body → obtains an access token → opens an HTTP client with that token → sends the POST request → passes the response through _body → returns the parsed dictionary.

**Call relations**: PipedreamClient.connect_token and PipedreamClient.run_action call this helper. It mirrors PipedreamClient._get, but for requests that send a body and cause something to happen.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 293–306)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Pipedream. When a token is provided, it adds the authorization header and the Pipedream environment header.

**Data flow**: It receives an optional access token → builds headers if the call is authenticated, or no headers for the token-grant call → returns an httpx asynchronous client with the base URL, timeout, headers, and optional test transport.

**Call relations**: PipedreamClient.access_token uses this without a token to request the first credential. PipedreamClient._get and PipedreamClient._post use it with a token for normal authenticated Connect API calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 309–310)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This avoids crashes or mistaken assumptions when Pipedream returns an unexpected shape.

**Data flow**: It receives any value → if the value is a dictionary, it returns it unchanged → otherwise it returns an empty dictionary.

**Call relations**: Several account and action-reading methods use this when pulling nested fields out of Pipedream responses. It is a small defensive helper that keeps response parsing predictable.

*Call graph*: called by 6 (account_label, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 313–326)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that a Pipedream account record belongs to the exact external user the caller expects. It then returns the account in the small ConnectedAccount form used by the rest of the extension.

**Data flow**: It receives an account record, account id, and expected external user id → first validates and converts the record with _account → compares the record’s owner with the expected user → returns the account if they match, or raises a forbidden error if they do not.

**Call relations**: PipedreamClient.connected_account and PipedreamClient.newest_account use this after fetching account data. It is the main per-user ownership guard before a connected account can be trusted.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 329–352)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into a ConnectedAccount, while rejecting records that cannot safely be used. In particular, it treats an unhealthy grant as something the user must reconnect, not as a temporary server problem.

**Data flow**: It receives a raw account record and account id → checks that the record has an external owner → checks whether Pipedream marks the account as unhealthy → reads the app slug if present → returns a ConnectedAccount with the id, app, and owner.

**Call relations**: This helper is used by _owned_account and PipedreamClient.workspace_account. It is the shared account-validation step underneath both user-level and workspace-level checks.

*Call graph*: calls 3 internal fn (__init__, __init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 355–356)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard beginning of external user ids that belong to a workspace. This gives the code one consistent naming pattern for Pipedream users tied to a workspace.

**Data flow**: It receives a workspace UUID → converts it to its compact hexadecimal form → returns a string prefix beginning with ufo_ and ending with an underscore.

**Call relations**: _workspace_owns_external_user uses this to recognize workspace-owned users, and connection_user_id uses it when creating a new state-specific external user id.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 359–368)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether an external user id is allowed to belong to a workspace. It supports both an older direct workspace id form and the newer prefixed form with a hashed connection id.

**Data flow**: It receives a workspace UUID and an external user id → first checks the direct legacy form → otherwise checks that the id starts with the workspace prefix → verifies that the remaining connection id is exactly the right length and made of lowercase hexadecimal characters → returns true or false.

**Call relations**: PipedreamClient.workspace_account calls this after reading an account’s owner. This function is the final yes-or-no ownership rule for workspace-scoped account use.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 371–373)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for one workspace and one connection state. The state is hashed so the resulting id is compact and does not reveal the original state string.

**Data flow**: It receives a workspace UUID and state string → hashes the state with SHA-256 → takes the first fixed-length part of the hexadecimal hash → appends it to the workspace user prefix → returns the full external user id.

**Call relations**: Consent-flow code can use this when starting a Pipedream connection, so that the account created by Pipedream can later be tied back to the workspace and connection attempt. It shares the same prefix format that _workspace_owns_external_user later validates.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 376–384)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Converts an HTTP response from Pipedream into a safe dictionary or raises a clear error. It is the common checkpoint for failed HTTP statuses and unexpected JSON shapes.

**Data flow**: It receives an HTTP response → if the status is an error, it raises PipedreamError with the status and text → if the body is empty, it returns an empty dictionary → otherwise it parses JSON and confirms the result is a dictionary → returns that dictionary or raises an error.

**Call relations**: PipedreamClient.access_token, PipedreamClient._get, and PipedreamClient._post all send responses through this function. That keeps response validation consistent across token minting, reads, and writes.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 387–405)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a PipedreamClient from environment variables set for the deployment. It fails early if the required Pipedream credentials or project id are missing.

**Data flow**: It reads the client id, client secret, project id, and optional environment name from environment variables → checks that the required three values are present → creates and returns a PipedreamClient configured for this deployment.

**Call relations**: Higher-level code uses this as the standard way to get a real Pipedream client. It is the setup doorway into this file’s API client, and it prevents connector OAuth flows from starting with incomplete configuration.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `connect request handling`

OAuth is the web pattern where a user grants an app permission to access another service, such as Gmail, without sharing their password. ufo expects this to start with a simple authorization URL and later finish with an exchange step. Pipedream works a little differently: before sending the user to its hosted connection page, the server must first ask Pipedream for a short-lived Connect token. That request is asynchronous, so this file creates a small browser bridge route to do the extra work at the right time.

The flow starts when `PipedreamOAuthProvider.authorize_url` sends the browser not directly to Pipedream, but to this extension's own `/ext/pipedream/oauth` route. That route checks the requested connector, creates a Pipedream Connect token for this exact workspace and sealed state, and redirects the user to Pipedream's hosted page. Think of it like a reception desk: ufo sends the visitor to the desk, the desk prints the right temporary pass, then points them to the correct building.

When Pipedream sends the browser back, `oauth_route` finds the newest account connected for that exact external user and redirects back to ufo core with the account id as the code. Finally, `exchange` asks Pipedream for that exact account again, verifies it belongs to the expected app, and returns a lightweight `OAuthAccount`. The actual access token stays inside Pipedream, so ufo does not store the user's third-party secret.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 51–53)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This starts the connection flow by building the URL the user's browser should visit first. Instead of pointing straight at Pipedream, it points at ufo's own bridge route so the system can mint a Pipedream Connect token before continuing.

**Data flow**: It receives a sealed `state` value and the core `redirect_uri` that ufo wants to return to later. It packages the provider name, state, and callback into URL query parameters, extracts the origin from the callback URL, and produces a full bridge URL such as `/ext/pipedream/oauth?...`. It does not contact Pipedream or change stored data.

**Call relations**: This is called at the beginning of ufo's connect flow when core needs a browser URL. It uses `_origin` to keep the bridge on the same scheme and host as the callback, and it uses URL encoding so the state and callback survive safely inside the link.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 55–69)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This finishes the connection from ufo core's point of view. Given an account id returned through the browser, it confirms that the account belongs to the right Pipedream app and turns it into an `OAuthAccount` record that ufo can bind to the grant.

**Data flow**: It receives the returned `code`, the workspace id, and the original state. From the workspace and state it rebuilds the Pipedream external user id, asks Pipedream for the connected account with that id, and checks that the account's app matches this provider. It then tries to fetch a friendly account label and returns an `OAuthAccount` containing the account id and optional label. If the app does not match, it raises a Pipedream error instead of accepting the connection.

**Call relations**: This is used after `oauth_route` has redirected back to core with an account id. It calls into the Pipedream client to retrieve and verify the exact account, which prevents one overlapping connection attempt from accidentally binding another attempt's account.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 72–120)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge route for both halves of Pipedream consent. On the way out, it creates a Pipedream Connect token and redirects the user to Pipedream; on the way back, it redirects the result to ufo core.

**Data flow**: It reads query parameters from the incoming web request: the provider, sealed state, callback URL, and optional outcome marker. If state or callback is missing, it returns a bad-request response; if the provider is unknown, it returns a not-found response. If Pipedream reports success, it finds the newest connected account for this exact workspace/state user and sends the browser back to the callback with the state and account id. If Pipedream reports failure, it returns an error page. If there is no outcome yet, it creates a Connect token with success and error redirects pointing back to this same route, builds the hosted Pipedream Connect Link for the requested app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: This route is reached first by the URL created in `PipedreamOAuthProvider.authorize_url`. It calls `_origin` to build safe return URLs, uses the connector registry to look up the Pipedream app, and calls the Pipedream client to create tokens or find connected accounts. Its successful return leg hands the account id to core, which later calls `PipedreamOAuthProvider.exchange` to verify and bind it.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 123–127)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the scheme and host from a URL, such as `https://example.com`, so bridge links can be built on the correct site. It also rejects callback URLs that are not usable web URLs.

**Data flow**: It receives a URL string, parses it into pieces, and checks that it has an `http` or `https` scheme plus a host name. If the URL is valid, it returns only the origin, meaning scheme plus host. If not, it raises an error explaining that the callback needs a scheme and host.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` rely on this helper when constructing browser redirects. It keeps those functions from duplicating URL validation and helps prevent malformed callback URLs from producing broken OAuth bridge links.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Token-Safe Provider Proxy
Routes provider HTTP requests through Pipedream so external services can be called without exposing or storing provider tokens.

### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and teardown`

Pipedream stores the real account credential, such as a Gmail or Slack token, and does not reveal it to this project. That is safer, but it creates a practical problem: a connector still needs to make normal HTTP requests to the provider. This file solves that by defining `PipedreamProxyTransport`, a custom HTTP transport. A transport is the low-level part of an HTTP client that actually sends requests.

When a connector asks to fetch a provider URL, this transport reads the original request, gets a Pipedream access token, and builds a new request to Pipedream's Connect Proxy endpoint. It hides the original provider URL inside the proxy path using base64-url encoding, which is a safe text form for putting data in a URL. It also adds the account ID and external user ID so Pipedream knows which stored credential to inject.

Headers need special care. Pipedream only forwards headers that start with `x-pd-proxy-`, so this file rewrites useful provider headers with that prefix and drops low-level transport headers like `host`, `content-length`, and `authorization`. The result is like giving a sealed letter to a trusted courier: the connector writes the provider request, Pipedream adds the private credential, and the provider's response comes back unchanged, including body, headers, and status code.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 55–74)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This function is called whenever an HTTP request should be sent through Pipedream instead of directly to the provider. It rewrites the request so Pipedream can add the stored account credential on the server side, then sends that rewritten request using the underlying transport.

**Data flow**: It starts with an ordinary HTTP request aimed at the provider. It reads the request body, asks the Pipedream client for an access token, keeps only the headers that are safe and useful, and prefixes those headers so Pipedream will forward them. It encodes the original provider URL into the Pipedream proxy URL, adds the external user ID and account ID as query information, creates a new HTTP request to Pipedream, and returns the response from the inner transport. The response is not translated; the caller receives the provider-style status, headers, and body that came back through Pipedream.

**Call relations**: This is the main workhorse of the transport. An `httpx.AsyncClient` calls it when a connector sends a request. Inside, it uses `request.aread` to collect the original body, `base64.urlsafe_b64encode` to make the original URL safe for the proxy path, and `httpx.URL` and `httpx.Request` to build the new Pipedream request. It then hands the request to `inner.handle_async_request`, which performs the actual network sending.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 76–77)

```
async def aclose(self) -> None
```

**Purpose**: This function closes the underlying HTTP transport when the proxy transport is no longer needed. It is used to release network resources cleanly.

**Data flow**: It receives no request data. It simply forwards the close instruction to the inner transport, which can then shut down any open connections or related resources. Nothing is returned.

**Call relations**: This is called during cleanup, usually when the HTTP client using this transport is being closed. Rather than doing its own cleanup work, it delegates to `inner.aclose`, because the inner transport owns the actual network resources.

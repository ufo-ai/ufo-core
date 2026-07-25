# Identity, sessions, OAuth, and credential connection  `stage-5`

This stage is shared behind-the-scenes support for answering the question: “Who is this request allowed to act as, and which outside accounts may it use?” It is used when a user connects services like Gmail or Slack, and later when an agent needs permission to use them.

The grants file is the permission ledger. It records which agent may use which connected account, and helps limit which outside websites the proxy may contact. The CLI surface provides the return point for OAuth, which is the common “sign in with another service” browser flow. After the outside provider sends the user back, it finishes the connection.

The Composio and Pipedream provider files are bridges to hosted consent pages. They send the user to those services to approve access, so this project does not store long-lived secret tokens. The Pipedream client is the guarded doorway to Pipedream itself. It creates consent links, checks the connected account matches the right user or workspace, runs actions, and reports API errors in a clear way.

## Files in this stage

### Pipedream connect entry
Starts a Pipedream-hosted account connection flow from ufo and hands authorization decisions into the grant layer.

### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during account connect/OAuth flow`

OAuth is the web pattern where a user grants an app permission to use another service, such as Gmail, without sharing their password. ufo expects a fairly simple OAuth shape: give the browser an authorization URL, then later exchange a returned code for an account. Pipedream’s flow is a little different because creating the Pipedream Connect Link needs an asynchronous API call. This file solves that mismatch.

Instead of sending the browser straight to Pipedream, `authorize_url` sends it to this extension’s own `/ext/pipedream/oauth` route. That route is the bridge. On the first visit, it creates a short-lived Pipedream connect token for a specific workspace and sealed state, then redirects the browser to Pipedream’s hosted connection page for the right app. Think of it like a reception desk that first prepares a visitor badge, then sends the visitor to the correct office.

When Pipedream sends the browser back, the same route checks whether the connection succeeded. If it did, it finds the newest account for that exact external user and sends the account id back to ufo core as the OAuth “code.” Later, `exchange` verifies that the account really belongs to the expected Pipedream app before returning an `OAuthAccount`. This prevents one overlapping connection attempt from accidentally claiming another user’s account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL that ufo gives to the user’s browser when an account connection starts. It deliberately points to this extension’s bridge route, not directly to Pipedream, because the real Pipedream link must be created with an asynchronous API call.

**Data flow**: It receives the sealed connect `state` and the final `redirect_uri` that ufo core expects later. It packages the provider name, state, and callback into query parameters, extracts the scheme and host from the callback URL, and returns a bridge URL under `/ext/pipedream/oauth`.

**Call relations**: This is the start of the flow for a `PipedreamOAuthProvider`. It calls `_origin` to reuse the same public origin as the callback URL, then the user’s browser follows the returned URL to `oauth_route`, where the Pipedream token can actually be minted.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–63)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This completes ufo’s side of the OAuth exchange after the browser has returned with an account id. It confirms that the account belongs to the correct Pipedream app before giving ufo core an `OAuthAccount` to bind to the grant.

**Data flow**: It receives a `code`, which in this flow is really the Pipedream account id, plus the workspace id and sealed state. It rebuilds the Pipedream external user id for that workspace and state, asks Pipedream for that exact connected account, checks that the account’s app matches this provider, and returns an `OAuthAccount` containing the account id. If the app does not match, it raises a Pipedream error instead of accepting the account.

**Call relations**: This runs after `oauth_route` has redirected back to core with a `code`. It relies on `connection_user_id` to tie the lookup to the same sealed state and uses the Pipedream client to fetch the account, so the final grant cannot silently attach an account from another app or another connection attempt.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 66–109)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser-facing bridge route for both halves of the Pipedream connection flow. It starts the Pipedream consent page on the first visit, and it processes Pipedream’s return visit after the user finishes or cancels consent.

**Data flow**: It reads query parameters from the incoming HTTP request: the provider, sealed state, callback URL, and optional outcome. If required values are missing, it returns an error response. If the provider is unknown, it returns a not-found response. If Pipedream reports success, it finds the newest connected account for this workspace/state and provider app, then redirects back to core with the original state and the account id as the code. If Pipedream reports failure, it returns a clear failure response. If there is no outcome yet, it creates a Pipedream connect token with success and error redirects pointing back to this same route, builds the Pipedream Connect Link for the provider’s app, optionally adds a custom OAuth app id from the environment, and redirects the browser to Pipedream.

**Call relations**: The flow reaches this route from `PipedreamOAuthProvider.authorize_url`. On the start leg, it calls `_origin` to build safe return URLs and calls the Pipedream client to create a connect token. On the return leg, it again uses the Pipedream client to resolve the account, then hands control back to ufo core through the callback URL so `PipedreamOAuthProvider.exchange` can finish the binding.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 112–116)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts just the public origin of a URL, meaning its scheme and host, such as `https://example.com`. It also rejects callback URLs that are not usable for browser redirects.

**Data flow**: It receives a URL string, parses it, and checks that it has either `http` or `https` plus a host name. If the URL is valid, it returns `scheme://host`. If not, it raises an error explaining that the callback needs a scheme and host.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` use this helper when building bridge URLs. It keeps the redirect-building code honest by making sure the bridge is based on a real web origin rather than a partial or unsafe URL.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Grant records and callbacks
Records which connected accounts agents may use and completes OAuth returns from external account providers.

### `core/src/ufo/grants.py`

`domain_logic` · `request handling and OAuth callback handling`

This file is the project’s “permission slip” system for external accounts. Instead of asking an operator to pre-load every credential, a user can grant access during a conversation through a `/connect` flow. The important safety rule is that the real OAuth token stays with an outside broker or provider; this code stores only a stable account ID, the provider name, the host that becomes reachable, and audit details about who granted it.

The flow has two halves, like handing someone a sealed envelope and checking it when they come back. `ConnectFlow.authorize` creates an OAuth link with sealed state inside it. That sealed state says which workspace, agent, provider, member, and conversation the grant is for. Later, `ConnectFlow.complete` opens and checks that state, asks the provider to exchange the returned OAuth code for a connected account, and records the grant.

`GrantStore` is the database-facing part. It saves grants, reads active grants for an agent, revokes grants, and changes whether a grant is shared. `ConnectHandoff` protects a private in-chat connect request so only the right member can use it, and so repeated clicks reuse the same authorization URL while it is still fresh. Without this file, agents could not safely gain user-approved access to external services during chat, and the proxy would not know which hosts to allow or refuse.

#### Function details

##### `grant_sentinel`  (lines 34–38)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Creates a special placeholder credential string for a connected account. The system can pass this harmless marker around instead of passing a real secret.

**Data flow**: It receives a connected account ID. It prefixes that ID with a fixed marker string and returns the combined sentinel value. It does not read or change any stored data.

**Call relations**: This helper gives different parts of the system a common way to recognize the same grant. The engine can place the sentinel in an environment variable, while the proxy can later match that sentinel to the brokered account.


##### `OAuthProvider.provider`  (lines 76–76)

```
def provider(self) -> str
```

**Purpose**: Names the OAuth provider, such as the connector identity used by the rest of the grant system. It is part of the provider interface that connector extensions must supply.

**Data flow**: A concrete provider object supplies this property. Code reads it to learn the provider name and stores or compares that name when creating grants.

**Call relations**: Provider implementations are injected into `ConnectFlow`. `ConnectFlow.complete` uses the provider name from the descriptor when recording the finished grant.


##### `OAuthProvider.host`  (lines 79–79)

```
def host(self) -> str
```

**Purpose**: Gives the external host that this provider grant should allow through the egress proxy. In plain terms, it says which internet destination becomes reachable after the grant.

**Data flow**: A concrete provider object supplies this property. Code reads the host and saves it on the grant so later proxy rules can be derived without asking the provider again.

**Call relations**: Provider implementations hand this value to `ConnectFlow.complete`, which passes it to `GrantStore.record`. Later, active grant readers use the stored host to decide what network access is allowed.


##### `OAuthProvider.authorize_url`  (lines 81–81)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the browser URL that a user opens to approve access with the external provider. This is the start of the OAuth handoff.

**Data flow**: It receives a sealed state string and a callback URL. It returns a provider-specific authorization link containing enough information for the provider to send the user back correctly.

**Call relations**: Called by `ConnectFlow.authorize` after the flow has sealed the grant details into the OAuth state. The returned URL is then shown or saved for the user to open.


##### `OAuthProvider.exchange`  (lines 83–85)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the short-lived OAuth code returned by the provider into the project’s connected-account identity. The real access token remains with the provider or broker, not in this grant record.

**Data flow**: It receives the OAuth code, callback URL, workspace ID, and state string. It verifies and exchanges those with the provider, then returns an `OAuthAccount` containing the stable account ID.

**Call relations**: Called by `ConnectFlow.complete` after the sealed state has been opened. Its returned account ID is passed directly into `GrantStore.record` so the account can be bound to the agent.


##### `GrantStore.record`  (lines 147–200)

```
async def record(self, *, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> None
```

**Purpose**: Saves a completed grant in the database, or updates the existing row if the same account was already connected for the same agent. It is the point where a successful OAuth handoff becomes durable system state.

**Data flow**: It receives the workspace, agent, provider, connected account ID, host, grantor member, conversation, and sharing choice. It first rejects account IDs containing control characters, then opens a workspace database transaction and inserts the grant; if the same grant already exists, it refreshes its host, audit fields, sharing flag, and update time. It returns nothing, but the database is changed.

**Call relations**: Called after `ConnectFlow.complete` receives an account from the provider exchange. It uses `workspace_tx` to write inside the current workspace database context and `uuid4` to create a row ID for new grants.

*Call graph*: 2 external calls (workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 202–231)

```
async def active_grants(self, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]
```

**Purpose**: Reads all grants for one agent in one workspace. This is how the system learns which connected accounts and hosts that specific agent may use.

**Data flow**: It receives a workspace ID and agent ID. It queries the grant table for matching rows, converts each row into a `Grant` object, and returns them as a tuple. It does not change the database.

**Call relations**: Called by `core/src/ufo/loop/queue._grant_cli_env` when preparing grant-related command-line environment data for an agent turn. It uses `workspace_tx` and a SQL select to fetch only that agent’s grant rows.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 233–246)

```
async def revoke(self, workspace_id: UUID, provider: str, account_id: str) -> bool
```

**Purpose**: Removes a connected provider account from a workspace’s grants. After this, tools and proxy rules should no longer resolve that account through the grant table.

**Data flow**: It receives a workspace ID, provider name, and account ID. It deletes every matching grant row in that workspace and returns `true` if at least one row was removed, otherwise `false`.

**Call relations**: This is the delete operation for grant-like connector objects. It uses `workspace_tx` and a SQL delete; because the broker keeps the real token, deleting these rows is the local way to withdraw access.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `GrantStore.set_shared`  (lines 248–265)

```
async def set_shared(self, workspace_id: UUID, provider: str, account_id: str, shared: bool) -> bool
```

**Purpose**: Changes whether a connected account is shared with other members’ turns or kept private to its grantor. It applies the choice to all rows for that provider account in the workspace.

**Data flow**: It receives a workspace ID, provider name, account ID, and the desired shared/not-shared value. It updates matching grant rows, refreshes their update time, and returns `true` if anything changed.

**Call relations**: This supports the share and unshare behavior for connector objects. Like `revoke`, it treats sharing as a property of the connected account within the workspace, so all matching grant bindings are updated together.

*Call graph*: 2 external calls (update, workspace_tx).


##### `ConnectFlow.authorize`  (lines 281–301)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Starts the OAuth connection flow by producing the provider URL the user should open. It seals the important grant details into the OAuth state so the callback can later trust what the request was for.

**Data flow**: It receives the workspace, agent, provider name, grantor member, conversation, and sharing choice. It looks up the provider, builds a `ConnectState`, encrypts that state with Fernet encryption, and asks the provider to build an authorization URL. The returned value is the browser link.

**Call relations**: This is used when a connect request needs a URL. It calls `_provider` to reject unknown providers before creating the state, then hands the sealed state to the provider’s `authorize_url` method.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 303–304)

```
def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks that a provider name is currently installed and usable. It is a small guard used before offering or reusing a connect request.

**Data flow**: It receives a provider name. It tries to look that name up in the installed provider map. It returns nothing if found, and raises an error if not.

**Call relations**: It delegates the actual lookup to `_provider`. `ConnectHandoff.authorize` uses this check so a stale terminal request cannot continue if its provider extension is no longer available.

*Call graph*: calls 1 internal fn (_provider).


##### `ConnectFlow.bridge_workspace`  (lines 306–312)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and identifies which workspace it is allowed to act for. This prevents a callback-like request from claiming the wrong provider, callback URL, or workspace.

**Data flow**: It receives a sealed state string, a provider name, and a callback URL. It opens the sealed state, compares the provider and callback against what was sealed and configured, checks that the provider exists, and returns the workspace ID from the trusted state. If anything does not match, it raises an invalid-state error.

**Call relations**: Called through `connect_bridge_workspace` for connector browser bridge requests. It uses `_open` to decode the state and `_provider` to ensure the provider is known.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 314–331)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth connection after the provider redirects back with a code. It verifies the original sealed request, exchanges the code for a connected account, and records the grant.

**Data flow**: It receives the sealed state and returned OAuth code. It opens the state, finds the provider, enters the correct workspace context, asks the provider to exchange the code, and writes the resulting account ID and host to the grant store. It returns a `GrantRecorded` summary with the provider, account ID, and agent ID.

**Call relations**: This is the second half of the flow started by `ConnectFlow.authorize`. It calls `_open`, `_provider`, the provider’s `exchange`, and then `GrantStore.record` inside `ufo.workspace.ws` so the database write happens for the right workspace.

*Call graph*: calls 2 internal fn (_open, _provider); 2 external calls (__init__, ws).


##### `ConnectFlow._provider`  (lines 333–337)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Looks up an installed OAuth provider by name. It gives the rest of `ConnectFlow` one consistent place to reject unknown providers.

**Data flow**: It receives a provider name. It reads the flow’s provider mapping and returns the matching provider descriptor. If there is no match, it raises `UnknownProvider`.

**Call relations**: Used by `ConnectFlow.authorize`, `ConnectFlow.validate_provider`, `ConnectFlow.bridge_workspace`, and `ConnectFlow.complete` whenever they need a trusted provider descriptor before continuing.

*Call graph*: called by 4 (authorize, bridge_workspace, complete, validate_provider); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 339–344)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Opens and verifies the sealed OAuth state that traveled through the browser. It rejects state that was changed, cannot be read, or is too old.

**Data flow**: It receives the encrypted state string. It asks Fernet to decrypt it with a ten-minute time limit, then parses the JSON into a `ConnectState`. If decryption fails or the state has expired, it raises `ConnectStateInvalid`.

**Call relations**: Used by `ConnectFlow.bridge_workspace` and `ConnectFlow.complete`, the two places that must trust data coming back from the browser before acting on it.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 353–438)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a private in-chat connect request into an OAuth authorization URL, while making sure the right member is using it and the request has not gone stale. It also reuses a previously generated URL during its valid lifetime.

**Data flow**: It receives a workspace ID, turn ID, and member ID. It locks and reads the matching turn row, checks that the speaker is the same member, checks that the terminal frame still contains a connect request, validates the provider, and checks expiration times. If a fresh URL was already saved, it returns it. Otherwise it asks `ConnectFlow.authorize` for a new URL, saves that URL and timestamp on the turn, and returns it. If another process saved the URL first, it rereads and returns the saved one.

**Call relations**: This function sits between the chat terminal request and the OAuth flow. It uses database transactions, `TerminalFrame.model_validate`, time checks, and SQL updates to make a one-member, short-lived handoff safe and repeatable.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 444–452)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow object. This gives tools, web surfaces, and callbacks one shared place to find the configured providers, encryption key, grant store, and redirect URL.

**Data flow**: It receives either a `ConnectFlow` or `None`. It stores that value in a module-level variable. It returns nothing, but future calls to `installed_connect_flow` will see the new value.

**Call relations**: Called during server setup, and also by tests that need to inject a fake flow. If `None` is installed, later readers fail clearly with `ConnectUnavailable`.


##### `installed_connect_flow`  (lines 455–458)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the currently installed connect flow, or raises a clear error if connecting accounts is not configured. It prevents callers from silently proceeding without the required credential setup.

**Data flow**: It reads the module-level installed flow variable. If a flow is present, it returns it. If not, it raises `ConnectUnavailable`.

**Call relations**: Called by `connect_bridge_workspace` before verifying a browser bridge request. Other surfaces can also use it when they need the configured OAuth connect machinery.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 461–470)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming browser bridge request belongs to a valid connect flow, and returns the trusted workspace if it does. If the request cannot be trusted, it returns `None` instead of raising outward.

**Data flow**: It receives a Starlette web request. It reads `state`, `provider`, and `callback` from the query string, gets the installed connect flow, and asks that flow to verify the bridge. On success it returns a workspace ID; on invalid state, missing setup, or unknown provider, it returns `None`.

**Call relations**: This is a web-facing wrapper around `installed_connect_flow().bridge_workspace`. It converts connect-flow errors into a simple accept-or-reject answer for browser bridge handling.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `grant_summaries`  (lines 473–508)

```
async def grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads a workspace’s grants in an audit-friendly form for commands and connector object views. It shows what was granted, to which agent, by whom, and when, without exposing any secret.

**Data flow**: It receives a workspace ID. It queries grant rows joined with the agent table so each grant includes the agent name, orders the rows by provider, converts them into `GrantSummary` objects, and returns them as a tuple. It does not change stored data.

**Call relations**: Used behind grant listing features such as `ufoctl grants`. It uses `workspace_tx` and a SQL select, but does not need any encryption key because grant rows contain account IDs and audit data, not OAuth tokens.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

When a user connects an outside service, such as a provider account, the system cannot finish everything inside the chat. The user is sent to the provider, signs in there, and the provider redirects their browser back to this callback address with two important pieces of information: a state value and a code. The state is like a sealed claim ticket: it proves which conversation, agent, and user started the connection. The code is the temporary token the provider gives back so the system can claim the account connection.

This file exposes that callback as a FastAPI route, which means it is part of the HTTP web surface. It does not use normal bearer authentication, because the browser arriving here is carrying the sealed state instead. The route checks that both state and code are present, asks the installed connection flow to complete the exchange, and turns expected failures into clear HTTP errors. If connection support is not available, it returns service unavailable. If the state is bad, it rejects the request. If the provider is unknown, it reports that the connector is not installed.

On success, it returns a plain text message telling the user which provider account was connected and to go back to chat.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP endpoint that receives the browser redirect after a provider OAuth login. It verifies the returned information, completes the account connection, and gives the user a simple success or error response.

**Data flow**: It receives two query values from the browser: state, which proves the connection was started by this system, and code, which is the provider's temporary authorization code. It first gets the installed connection flow, then rejects the request if either value is missing. It asks the flow to complete the connection using the state and code. If that succeeds, it returns plain text naming the connected provider and account; if something expected is wrong, it raises an HTTP error with the right status code.

**Call relations**: FastAPI calls this function when a request reaches /v1/connect/callback. The function calls ufo.grants.installed_connect_flow to find the OAuth connection machinery, then delegates the real completion work to that flow. It uses FastAPI's HTTPException to turn known problems into web responses, and PlainTextResponse to send the final success message back to the browser.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### Hosted connector adapters
Bridges ufo account connection to hosted connector services that manage user consent and provider tokens safely.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `request handling during connector OAuth setup`

OAuth is the common “Sign in with…” style flow where a user gives permission to connect an outside service. ufo expects one kind of OAuth flow: first give the browser an authorization URL, then later exchange a returned code for an account. Composio works a little differently because creating its consent link requires an asynchronous API call. This file smooths over that mismatch.

The main idea is a browser detour. Instead of sending the browser straight to Composio, `authorize_url` sends it to this extension’s own `/ext/composio/oauth` route. That route can do the slower work: it asks Composio for a connect link, tied to the current workspace, then redirects the browser to Composio’s consent screen.

After the user finishes, Composio sends the browser back to the same route with a `connected_account_id`. The route then redirects to ufo’s real callback and passes that account id as the OAuth `code`. Finally, `exchange` checks with Composio that the account belongs to this workspace’s brokered user and matches the expected toolkit before ufo binds it.

A key safety point: the actual third-party token stays inside Composio. ufo stores a connected account reference, not the secret itself.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 44–46)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the URL that ufo should send the member’s browser to when starting a Composio-backed connection. Instead of pointing directly at Composio, it points at this extension’s bridge route so the async Composio link can be created there.

**Data flow**: It receives a sealed `state` value from ufo and the final `redirect_uri` that ufo wants the browser to return to. It extracts the scheme and host from that redirect URI, adds the provider name, state, and callback as query parameters, and returns a full bridge-route URL for the browser.

**Call relations**: This is the first step in the connect story. ufo calls it when it needs an authorization URL. It uses `_origin` to find the safe base web address and `urlencode` to package the query values, then the browser later lands in `oauth_route` to continue the flow.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 48–52)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This completes the connection after Composio has returned a connected account id. It asks Composio to confirm that this account belongs to the current workspace’s Composio user and is for the expected toolkit before ufo accepts it.

**Data flow**: It receives the returned `code`, which in this flow is really a Composio connected account id, plus the workspace id. It builds the expected Composio external user id for that workspace, asks the Composio client for the connected account, and returns an `OAuthAccount` if Composio confirms it.

**Call relations**: This runs after `oauth_route` has forwarded the connected account id into ufo’s normal callback as the `code`. It hands the verification work to `composio_client().connected_account`, which prevents a random or foreign account id from being bound to the workspace.

*Call graph*: 1 external calls (composio_client).


##### `oauth_route`  (lines 55–93)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge for both halves of the Composio consent trip. On the way out, it creates a Composio connect link and redirects the browser there; on the way back, it passes Composio’s connected account id into ufo’s normal OAuth callback.

**Data flow**: It reads query parameters from the incoming HTTP request, especially `state` and `callback`. If a connected account id is present, it redirects to the callback with that id as `code`. If Composio reports a status without an account id, it returns an error message instead of silently restarting. If this is the first visit, it looks up the requested provider, builds a return URL back to itself, asks Composio for a connect link tied to the workspace id, and redirects the browser to that link.

**Call relations**: The browser reaches this route after `ComposioOAuthProvider.authorize_url` sends it here. During the start leg, it uses `CONNECTORS.get` to find the provider details, `_origin` to build a callback-safe route, and `composio_client().connect_link` to get the Composio consent URL. During the return leg, it redirects onward to core ufo so `ComposioOAuthProvider.exchange` can verify and bind the connected account.

*Call graph*: calls 1 internal fn (_origin); 4 external calls (Response, get, composio_client, urlencode).


##### `_origin`  (lines 96–100)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the base origin from a URL, meaning the scheme and host such as `https://example.com`. It also rejects callback URLs that do not include a proper web scheme and host, because the OAuth bridge needs an absolute address.

**Data flow**: It receives a URL string, parses it, checks that it starts with `http` or `https` and has a host name, then returns only the scheme and host. If the URL is not usable as a web callback base, it raises an error instead of building a broken or unsafe redirect.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` call this helper when they need to construct bridge URLs from an existing callback address. It relies on `urlparse` for the parsing, then gives the callers a clean origin they can safely append the Composio OAuth route to.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector OAuth setup and connector action execution`

This file solves a sensitive problem: users need to connect accounts such as Gmail, but the project should not store or even read the real Google access token. Pipedream keeps that secret on its side. This code only stores and checks Pipedream’s connected-account id, which is more like a coat-check ticket than the coat itself.

The main piece is PipedreamClient. It talks to Pipedream’s web API using httpx, an asynchronous HTTP library. Before each normal API call, it gets a Pipedream access token using the deployment’s client id, client secret, and project id from the environment. That access token is cached until it is close to expiring, so the system does not ask Pipedream for a new one every time.

The file also has important safety checks. Pipedream project credentials can read many connected accounts in the project, so the code verifies ownership before using an account. It checks the account’s external user id and, for workspace-wide use, checks that the id matches the workspace’s expected naming pattern. Without these checks, one user or workspace could accidentally cause the system to use another account’s connection.

Finally, it exposes catalog and execution calls: list actions, fetch an action definition, and run an action server-side with Pipedream injecting the stored credentials.

#### Function details

##### `PipedreamError.__init__`  (lines 76–79)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception when Pipedream fails or returns data this client cannot safely use. It keeps both the status code and response text so callers can report or react to the exact failure.

**Data flow**: It receives a numeric status and a body string → builds a readable error message like “pipedream 403: ...” → stores the status and body on the error object for later inspection.

**Call relations**: This error is raised throughout the client when a response is bad, missing required fields, unhealthy, or owned by the wrong user. Broker code also raises it when connector setup or execution cannot safely continue.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 117–138)

```
async def access_token(self) -> str
```

**Purpose**: Gets the short-lived Pipedream access token that allows this deployment to call Pipedream’s Connect API. It reuses a cached token when it is still safely valid, which avoids unnecessary token requests.

**Data flow**: It reads the client id from the client object and checks the process-wide token cache → if a still-valid token exists, it returns it → otherwise it posts the client id and secret to Pipedream’s OAuth token endpoint, checks the response body, saves the new token and expiry time, and returns the token string.

**Call relations**: The lower-level request helpers, PipedreamClient._get and PipedreamClient._post, call this before making authenticated Pipedream requests. It uses PipedreamClient._http to create the HTTP client and _body to turn the HTTP response into a checked Python dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 140–155)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a Pipedream Connect token and hosted consent link for a specific external user. This is what lets a user open Pipedream’s consent page and connect an app account without this project handling the provider password or token.

**Data flow**: It receives an external user id plus success and error redirect URLs → sends them to Pipedream’s connect-token endpoint → checks that Pipedream returned both a token and a connect link URL → returns a ConnectToken object containing those two values.

**Call relations**: This public client method uses PipedreamClient._post for the authenticated API call. If Pipedream’s answer lacks the token or link, it raises PipedreamError instead of returning an unusable consent setup.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 157–164)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Fetches one connected account and proves it belongs to the expected external user before returning it. This is a key safety check against using someone else’s connection by mistake.

**Data flow**: It receives a connected-account id and an expected external user id → asks Pipedream for that account → extracts the useful account record → passes it through the ownership check → returns a ConnectedAccount only if the owner matches and the account is healthy.

**Call relations**: This method uses PipedreamClient._get to read from Pipedream, _dict to safely interpret nested response data, and _owned_account to enforce ownership. It is part of the guardrail before any connector grant is trusted.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.workspace_account`  (lines 166–176)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Fetches a connected account and checks that it belongs to the requested workspace. This allows workspace-level connector use while still blocking accounts connected under another workspace.

**Data flow**: It receives an account id and workspace UUID → reads the account from Pipedream → converts the response into a ConnectedAccount → checks whether the account’s external user id follows the workspace’s allowed pattern → returns the account if allowed, or raises an error if not.

**Call relations**: It uses PipedreamClient._get for the remote read, _account to validate the account record, and _workspace_owns_external_user for the workspace ownership rule. If the ownership test fails, it raises PipedreamError.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 178–192)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for one external user and app. This is useful right after a user finishes the consent flow, when the system needs to discover which account was just connected.

**Data flow**: It receives an external user id and app name → asks Pipedream for matching accounts → turns returned list items into dictionaries → chooses the record with the newest creation timestamp → checks that it has an id and belongs to the expected external user → returns a ConnectedAccount.

**Call relations**: It calls PipedreamClient._get to search accounts and _owned_account to enforce the user match. It raises PipedreamError if no matching account exists or if Pipedream’s record is missing the account id.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 194–200)

```
async def list_actions(self, app: str, query: str='', limit: int=ACTION_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Searches Pipedream’s catalog of available actions for a given app, such as Gmail. These actions are the ready-made operations that the connector can later describe or run.

**Data flow**: It receives an app slug, an optional search query, and a limit → builds query parameters for Pipedream → sends a GET request → returns Pipedream’s catalog response as a dictionary.

**Call relations**: PipedreamBroker._key_miss calls this when it needs to search for an action key that is not already known. This method delegates the actual HTTP work and response checking to PipedreamClient._get.

*Call graph*: calls 1 internal fn (_get); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 202–203)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition of one Pipedream action. The definition explains what inputs the action expects and how it should be configured.

**Data flow**: It receives an action/component key → asks Pipedream for that component → returns the checked response dictionary.

**Call relations**: This is a public lookup method for code that needs action details. It relies on PipedreamClient._get to authenticate, send the request, and parse the response.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 205–223)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on Pipedream’s servers for a specific external user. Pipedream injects the connected account credential on its side, so this project supplies configuration but not the provider token.

**Data flow**: It receives an action key, external user id, and configured action inputs → builds the Pipedream run request and asks for a fresh file stash → checks that the JSON request is not larger than the allowed payload size → posts the run request → returns Pipedream’s action result.

**Call relations**: This method delegates the HTTP call to PipedreamClient._post. It is designed for connector execution paths, including broker execution, where the action should run server-side with files made available through Pipedream’s file-stash export mechanism.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 225–228)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked response body. It is the shared helper for read-style API calls.

**Data flow**: It receives an API path and optional query parameters → gets a valid access token → opens an HTTP client with authentication headers → sends the GET request → converts the response into a dictionary or raises an error.

**Call relations**: Higher-level methods such as connected_account, workspace_account, newest_account, list_actions, and action_definition call this instead of repeating authentication and response parsing each time. It uses access_token, _http, and _body as its three building blocks.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 5 (action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 230–233)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked response body. It is the shared helper for create-or-run API calls.

**Data flow**: It receives an API path and a JSON-ready body dictionary → gets a valid access token → opens an authenticated HTTP client → sends the POST request → returns the parsed response dictionary or raises an error.

**Call relations**: PipedreamClient.connect_token uses this to mint consent links, and PipedreamClient.run_action uses it to execute actions. Like _get, it centralizes token use, HTTP setup, and response checking.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 235–248)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates the temporary HTTP client used for one Pipedream call. When a token is supplied, it adds the authorization header and the Pipedream environment header.

**Data flow**: It receives an optional access token → builds headers if the request is authenticated → returns an httpx AsyncClient configured with Pipedream’s base URL, timeout, optional test transport, and headers.

**Call relations**: PipedreamClient.access_token calls it without a token for the OAuth token request. PipedreamClient._get and PipedreamClient._post call it with a token for normal authenticated Connect API requests.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 251–252)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This prevents malformed Pipedream responses from causing confusing type errors later.

**Data flow**: It receives any value → if the value is a dictionary, it returns it unchanged → otherwise it returns an empty dictionary.

**Call relations**: Account-reading code uses this helper when Pipedream may wrap data inside nested objects. It is called by connected_account, workspace_account, newest_account, and _account before those functions read dictionary fields.

*Call graph*: called by 4 (connected_account, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 255–268)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that a Pipedream account record is healthy and belongs to one exact external user. It is the direct user-level ownership guard.

**Data flow**: It receives an account record, account id, and expected external user id → converts the record into a ConnectedAccount using _account → compares the account’s owner to the expected owner → returns the account if they match or raises PipedreamError if they do not.

**Call relations**: PipedreamClient.connected_account and PipedreamClient.newest_account call this after fetching account records from Pipedream. It depends on _account for basic account validation, then adds the stricter owner match.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 271–283)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into the project’s small ConnectedAccount shape, while rejecting unusable accounts. It checks that the account has an owner and is not marked unhealthy.

**Data flow**: It receives a raw account dictionary and account id → reads the external owner, health flag, and app slug → raises PipedreamError if the owner is missing or the account is unhealthy → returns a ConnectedAccount with the account id, app name, and owner id.

**Call relations**: Workspace account checks call this directly, and _owned_account calls it before checking the exact owner. It uses _dict to safely inspect the nested app object.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 286–287)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used for external user ids that belong to a workspace. This gives the system a predictable naming pattern for later ownership checks.

**Data flow**: It receives a workspace UUID → converts the UUID to its compact hexadecimal form → returns a string beginning with the project’s external-user prefix and ending with an underscore.

**Call relations**: _workspace_owns_external_user uses this prefix to verify account ownership, and connection_user_id uses it when creating a new connection-scoped external user id.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 290–299)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether an external user id should be considered part of a workspace. It accepts both an older simple workspace id form and the newer prefix-plus-connection-id form.

**Data flow**: It receives a workspace UUID and an external user id → first checks the direct legacy form → otherwise builds the workspace prefix and checks that the id starts with it → verifies that the remaining connection id is 32 lowercase hexadecimal characters → returns true or false.

**Call relations**: PipedreamClient.workspace_account calls this before allowing a workspace to use a connected account. It uses workspace_user_prefix to keep the naming rule consistent with id creation.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 302–304)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable external user id for one workspace connection flow from a state string. This lets the system tie a consent return back to the right workspace-scoped connection without exposing secrets.

**Data flow**: It receives a workspace UUID and a state string → hashes the state with SHA-256, a one-way fingerprinting function → keeps the first 32 hexadecimal characters → joins that connection id to the workspace prefix → returns the external user id string.

**Call relations**: It uses workspace_user_prefix so generated ids match the pattern checked by _workspace_owns_external_user. It is a helper for code that starts or tracks a Pipedream connection flow.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 307–315)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe Python dictionary. It rejects error responses and unexpected response shapes loudly.

**Data flow**: It receives an httpx response → if the status code is 400 or higher, it raises PipedreamError with the status and raw text → if the response is empty, it returns an empty dictionary → otherwise it parses JSON and confirms the result is a dictionary → returns that dictionary or raises an error.

**Call relations**: PipedreamClient.access_token, PipedreamClient._get, and PipedreamClient._post call this after every Pipedream HTTP response. It is the common checkpoint that keeps bad remote responses from flowing into the rest of the connector logic.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 318–336)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds the default PipedreamClient for this deployment from environment variables. It fails immediately if the required Pipedream credentials or project id are missing.

**Data flow**: It reads the client id, client secret, project id, and optional environment name from process environment variables → checks that the three required values are present → creates and returns a PipedreamClient configured for this deployment.

**Call relations**: This factory is used by higher-level connector code when it needs the real deployment client rather than a test client. It calls PipedreamClient.__init__ with the collected configuration.

*Call graph*: 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-identity-and-session-tokens` — The identities, bearer tokens, gateway tokens, operator sessions, and other passes that prove who is allowed in.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-oauth-consent-flow-state` — Short-lived OAuth/provider consent attempt state linking redirects, callbacks, workspace/member identity, and provider account checks until a credential is finalized.
- `reg-secret-keyring` — Loaded signing and encryption key material used to mint/verify tokens and seal/unseal protected secrets across trusted paths.
- `reg-http-client-pools` — Shared outbound HTTP client/session pools and retry-capable transport state used for provider APIs, OAuth/credential bridges, connectors, model calls, billing, email, and other integrations.

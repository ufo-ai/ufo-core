# Account-link and OAuth callback intake  `stage-5.1`

This stage is shared behind-the-scenes support for connecting outside accounts to a ufo workspace. It is used when a member clicks “connect” for a service, approves access in a browser, and comes back to ufo. The key job is to prove the account really belongs to the right person or workspace before anything is saved or granted.

The Composio and Pipedream provider files act like front doors to hosted consent services. They send the user to the right approval page, receive the return signal, and translate that result into a connection ufo can use. The grants file is the record keeper and permission guard: it tracks who owns each connected account, which agents may use it, and how to safely finish the browser sign-in. The CLI surface provides the small “you’re done” web page after approval. The GitHub coding connector verifies that a GitHub App installation truly belongs to the current user’s organization. The iMessage tool confirms a phone number before attaching it, preventing accidental or unwanted messaging.

## Files in this stage

### Hosted consent bridges
Browser-facing connector bridges send users through hosted Composio and Pipedream consent flows before handing successful connections back to ufo grants.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `connect request and OAuth callback handling`

ufo expects an OAuth provider to give it a ready-to-open authorization URL and later exchange a returned code for an account. Composio works a little differently: creating the consent link requires an asynchronous API call. This file solves that mismatch by adding a small detour route inside the extension.

The flow is like a receptionist redirecting visitors. First, `ComposioOAuthProvider.authorize_url` does not point straight to Composio. It points the browser to this extension’s `/ext/composio/oauth` route and includes the provider name, the sealed `state`, and the core callback address. The `state` is important because it ties the consent result back to the right user and conversation.

When `oauth_route` is opened the first time, it asks Composio to create a consent link for this workspace’s Composio user, then redirects the browser there. After the user finishes, Composio sends the browser back to the same route with a `connected_account_id`. The route then redirects to ufo’s normal callback, passing that account id as the OAuth `code`.

Finally, `ComposioOAuthProvider.exchange` checks with Composio that the account belongs to the expected workspace user and provider before returning an `OAuthAccount`. The actual provider token stays with Composio, so this code never stores a secret token.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the URL that ufo should send the user to when starting a Composio-backed connection. Instead of going directly to Composio, it sends the browser to this extension’s bridge route so the async Composio link can be created there.

**Data flow**: It receives the saved `state` value and the core `redirect_uri` callback. It extracts the origin, meaning the scheme and host such as `https://example.com`, then adds the provider name, state, and callback as query parameters. It returns a full URL pointing at `/ext/composio/oauth` on the same origin.

**Call relations**: This is the first step in the connect flow. It calls `_origin` to make sure the callback has a usable web origin, and it uses URL encoding so the provider, state, and callback can safely travel in the browser URL. The returned URL later lands in `oauth_route`.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected account id into ufo’s internal account record. It also verifies that the account belongs to the expected workspace user and provider, which prevents someone from injecting another account id.

**Data flow**: It receives `code`, which in this flow is really Composio’s connected account id, plus the workspace id. It builds the expected Composio external user id for that workspace, asks the Composio client to fetch and verify the connected account, then tries to fetch a human-friendly label. It returns an `OAuthAccount` containing the verified account id and, if available, the label.

**Call relations**: This runs after `oauth_route` has redirected back to ufo’s normal callback with the connected account id as `code`. It uses `composio_client` to ask Composio for the authoritative account information, then hands ufo an `OAuthAccount` so the connection can be bound without ever reading or storing the provider’s token.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge for both halves of Composio consent. It starts consent by creating a Composio connect link, and it finishes consent by forwarding Composio’s returned account id to ufo’s normal callback.

**Data flow**: It reads query parameters from the incoming browser request: `state`, `callback`, provider, `connected_account_id`, and status. If required state or callback data is missing, it returns an error response. If a connected account id is present, it redirects to the callback with that id as `code`. If Composio reports a status but no account id, it returns an explicit failure message. Otherwise, it creates a return URL back to itself, asks Composio for a provider-specific connect link for the current workspace user, and redirects the browser to that link.

**Call relations**: This route is reached first from `ComposioOAuthProvider.authorize_url`. On the start leg, it calls `_origin` and `composio_client().connect_link` to produce the real Composio consent destination. On the return leg, after Composio redirects back here, it sends the browser onward to core’s callback so `ComposioOAuthProvider.exchange` can verify and bind the account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the base web origin from a full URL, such as turning `https://site.example/path` into `https://site.example`. It also rejects callback URLs that are not normal HTTP or HTTPS web addresses.

**Data flow**: It receives a URL string and parses it into pieces. If the URL does not have an `http` or `https` scheme or does not include a host, it raises an error. Otherwise, it returns only the scheme and host.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` use this helper before building bridge URLs. It keeps those URLs anchored to the same trusted web origin as the callback, rather than blindly joining paths onto an invalid or incomplete URL.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `connector OAuth consent flow`

When a user needs to connect something like Gmail, ufo expects a simple OAuth flow: make an authorization URL, send the browser there, then exchange the returned code for an account. Pipedream works a little differently. Before the browser can go to Pipedream, the server must first ask Pipedream for a temporary Connect token, and that is an asynchronous API call. This file solves that mismatch.

The main class, PipedreamOAuthProvider, presents the shape ufo expects. Its authorization URL does not go straight to Pipedream. Instead, it points the browser to this extension's own `/ext/pipedream/oauth` route. That route then creates the Pipedream Connect token, builds success and error return URLs back to itself, and redirects the browser to Pipedream's hosted connection page.

On the way back, the route checks whether the user really connected an account. If they did, it finds the newest account for this exact workspace and state, then redirects back to core ufo with that account id as the code. Later, exchange verifies the account belongs to the expected Pipedream app before returning an OAuthAccount to core. This extra checking matters because browser callbacks can overlap; the file makes sure one user's connection cannot accidentally bind to another flow's account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the URL where the user's browser should start the connection process. Instead of sending the browser directly to Pipedream, it sends it to this extension's bridge route so the server can first create the needed Pipedream Connect token.

**Data flow**: It receives a sealed state value and the core callback URL. It packs the provider name, state, and callback into query parameters, extracts the origin such as `https://example.com` from the callback URL, and returns a bridge URL under `/ext/pipedream/oauth`. It does not contact Pipedream or change stored data.

**Call relations**: Core ufo calls this when it needs an authorization URL for a connector. The function uses `_origin` to keep the bridge on the same web origin as the callback, then hands the browser off to `oauth_route`, which performs the slower Pipedream setup.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–68)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the account id returned from the bridge flow into the OAuth account object that core ufo can bind to a grant. It also double-checks that the account belongs to the expected Pipedream app, so the wrong connector cannot be attached by mistake.

**Data flow**: It receives the returned code, workspace id, and state. It derives the Pipedream external user id for that workspace and state, asks Pipedream for the connected account with that exact id, and rejects it if the account's app does not match this provider. It then tries to fetch a human-friendly account label and returns an OAuthAccount containing the account id and optional label.

**Call relations**: Core ufo calls this after `oauth_route` redirects back with an account id as the code. It relies on the Pipedream client helpers to find and verify the account, then hands a clean OAuthAccount back to core so the grant can be stored without ufo ever storing the provider's secret token.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 71–114)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Runs the browser-facing bridge for both halves of the Pipedream connection flow. It starts consent by creating a Pipedream Connect token, and it finishes consent by redirecting core ufo back to the exact connected account.

**Data flow**: It reads query parameters from the incoming request: provider, state, callback, and optionally an outcome. If state or callback is missing, it returns a bad-request response. If the provider is unknown, it returns not found. If Pipedream reports a successful connection, it finds the newest account for this workspace-and-state external user and redirects to the original callback with the state and account id. If Pipedream reports failure, it returns an error message. If there is no outcome yet, it creates a Pipedream Connect token with success and failure URLs pointing back to this same route, adds the requested app and optional custom OAuth app id, and redirects the browser to Pipedream.

**Call relations**: The flow reaches this route from `PipedreamOAuthProvider.authorize_url` during the start leg, and from Pipedream during the return leg. It calls `_origin` when building its own bridge URL, uses the connector registry to find the app settings, talks to the Pipedream client to create tokens or find accounts, and finally returns HTTP responses that move the user's browser to the next step.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 117–121)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the safe web origin from a full URL, meaning just the scheme and host such as `https://chat.example.com`. This keeps bridge URLs anchored to the same site as the callback.

**Data flow**: It receives a URL string, parses it into pieces, and checks that it has an `http` or `https` scheme plus a host. If the URL is valid, it returns `scheme://host`. If not, it raises an error explaining that the callback needs a scheme and host.

**Call relations**: `PipedreamOAuthProvider.authorize_url` uses this to build the first bridge URL, and `oauth_route` uses it to build return URLs for Pipedream. In both cases, it acts like a gatekeeper that refuses malformed callback URLs before they are used in browser redirects.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Grant completion intake
Core grant logic records connected external accounts and agent permissions, while the CLI surface provides the post-OAuth landing page.

### `core/src/ufo/grants.py`

`domain_logic` · `request handling, OAuth callback, grant lookup, admin/user connection changes`

This file is the project’s “permission desk” for connected accounts. A member may connect an outside service through OAuth, which is the common browser flow where a service asks “Do you allow this app?” The system must then remember two separate facts: the member owns the connected account, and one or more agents are allowed to use it. Without this file, agents would not know which accounts they can act through, private connections could be accidentally shared, and browser callbacks could not be trusted.

The main flow starts with ConnectFlow.authorize, which builds a provider sign-in URL. It seals important details, like the workspace, agent, member, provider, and conversation, into a short-lived state token. Later, ConnectFlow.complete opens that sealed state, exchanges the provider’s returned code for a stable account identity, records the connection and grant in the database, notifies extension hooks, and optionally resumes the waiting conversation.

GrantStore is the database-facing part. It creates or reuses connection rows, adds or removes agent grant edges, checks ownership and admin rights, and cleans up related feed sources when a connection is disconnected. Helper functions produce safe names, summaries for user interfaces, and deterministic sentinel strings used when grants appear as placeholder credentials.

#### Function details

##### `grant_sentinel`  (lines 43–47)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a predictable placeholder credential name for a connected account. The system can pass this harmless marker around instead of passing a real secret.

**Data flow**: It receives an account ID string, prefixes it with a fixed marker, and returns the combined string. Nothing is stored or changed.

**Call relations**: This helper gives both sides of the system a shared way to recognize the same grant without registering it in advance: the sandbox can export the marker, and the egress side can recognize it as belonging to a brokered connection.


##### `OAuthProvider.provider`  (lines 94–94)

```
def provider(self) -> str
```

**Purpose**: Names the provider represented by an OAuth connector. This is the stable internal label used when storing and looking up connections.

**Data flow**: An implementation supplies this property. Callers read it and use the returned string as the provider identity in grant and connection records.

**Call relations**: ConnectFlow uses provider descriptors during authorization and completion; this property is part of the contract every installed provider must satisfy.


##### `OAuthProvider.host`  (lines 97–97)

```
def host(self) -> str
```

**Purpose**: Gives the provider host that a granted connection is allowed to reach. In plain terms, it says which outside destination this connection is for.

**Data flow**: An implementation supplies this property. The grant-recording path reads the string and stores it with the connection.

**Call relations**: ConnectFlow.complete reads this through the provider descriptor and passes it into GrantStore.record so later egress decisions know the relevant host.


##### `OAuthProvider.authorize_url`  (lines 99–99)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the web link a member opens to approve access with the provider. This is the first half of the OAuth handoff.

**Data flow**: It receives a sealed state string and a callback URL, combines them according to the provider’s rules, and returns a browser URL.

**Call relations**: ConnectFlow.authorize calls this after preparing the sealed state. The returned URL is shown to the member or memoized by ConnectHandoff.


##### `OAuthProvider.exchange`  (lines 101–103)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the provider’s returned authorization code into the system’s stable connected account identity. This is where the provider confirms which account was connected.

**Data flow**: It receives the returned code, callback URL, workspace ID, and original state. It talks to the provider implementation and returns an OAuthAccount containing the broker-side account ID and possibly a friendly label.

**Call relations**: ConnectFlow.complete calls this after verifying the state. Its result is then written into the grant database through GrantStore.record.


##### `OAuthProviderResolver.claims`  (lines 114–114)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open-ended connector broker recognizes a provider name. This lets the system support provider slugs that were not individually registered at startup.

**Data flow**: It receives a provider string, validates it against the resolver’s catalog or rules, and returns true or false.

**Call relations**: ConnectFlow.validate_provider uses this when a provider is not in the fixed provider map, so bad provider names can be rejected before making a dead sign-in link.


##### `OAuthProviderResolver.descriptor`  (lines 116–116)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuth provider descriptor for a provider name claimed by the resolver. It gives ConnectFlow the same shape of object as a normal registered provider.

**Data flow**: It receives a provider string and returns an OAuthProvider-like descriptor. It does not itself complete the OAuth exchange; it supplies the object that can.

**Call relations**: ConnectFlow._provider falls back to this resolver when a provider is not explicitly installed in the provider map.


##### `ConnectionHooks.fire`  (lines 216–216)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Notifies extensions that a connection has been recorded. Extensions can then create or refresh derived state, such as feed source rows.

**Data flow**: It receives a ConnectionRecorded payload describing the new or reused connection and performs extension-side work. It returns no useful data to the caller.

**Call relations**: ConnectFlow.complete calls this after GrantStore.record succeeds and before resuming the conversation, so follow-up work can see the connection as already present.


##### `ConnectResumption.resume`  (lines 229–236)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Tells the conversation that requested a connection that the connection has landed. This lets the agent continue instead of waiting silently after the member returns from the browser.

**Data flow**: It receives a conversation ID, a message, the speaking member ID, and an idempotency key, which is a repeat-safe key that prevents duplicate messages. It returns whether the resume message was accepted.

**Call relations**: ConnectFlow.complete calls this last, after the connection and extension-derived state have been recorded, so the resumed agent sees a ready-to-use workspace.


##### `_resume_key`  (lines 239–254)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a repeat-safe key for the conversation-resume message tied to one OAuth attempt. It avoids sending duplicate resume messages if the callback is refreshed.

**Data flow**: It receives the sealed OAuth state string, hashes it with SHA-256, keeps a short prefix of the digest, adds a fixed text prefix, and returns the resulting key.

**Call relations**: ConnectFlow.complete uses this when calling ConnectResumption.resume. The key is based on the one-time state rather than the connection row, because the same connection can be reused by later connect attempts.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 276–277)

```
def workspace_id(self) -> UUID
```

**Purpose**: Reads the workspace ID currently active in the request or task. GrantStore uses this so database changes are always scoped to the current workspace.

**Data flow**: It reads the current workspace context and returns its workspace UUID. It does not change data.

**Call relations**: GrantStore methods use this property while querying or writing connection and grant rows, keeping each operation inside the active workspace boundary.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 280–281)

```
def agent_id(self) -> UUID
```

**Purpose**: Reads the agent currently targeted by object dispatch. This decides which agent receives or loses a grant.

**Data flow**: It reads the current object-scope agent ID and returns that UUID. It does not modify anything.

**Call relations**: GrantStore methods use this property when creating, listing, or checking connector grants for the bound agent.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 283–378)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None) -> UUID
```

**Purpose**: Creates or reuses a member-owned connection and grants the current agent access to it. It protects ownership so one member cannot silently take over another member’s connected account.

**Data flow**: It receives provider, account, host, owner member, conversation, sharing choice, and optional account label. It writes or updates a connection row, checks that the same member owns any existing row, writes or refreshes the agent grant edge, and returns the connection ID.

**Call relations**: ConnectFlow.complete calls this after the provider exchange succeeds. The returned connection ID is then used to notify ConnectionHooks and build the final GrantRecorded result.

*Call graph*: 7 external calls (__init__, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 380–417)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the connections that the current agent is allowed to use. This is the agent’s usable view of member-owned accounts.

**Data flow**: It reads the current workspace and agent, joins grant rows to their connection rows, and returns Grant objects with provider, account, host, owner, and sharing information.

**Call relations**: The sandbox environment builder calls this when preparing credential markers for an agent run, so the running agent only receives markers for grants it actually has.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 419–431)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the acting member is allowed to do so. The connection itself can remain for other agents.

**Data flow**: It receives a grant ID and actor member ID. It checks whether that actor owns or may administer the underlying connection, deletes the grant row if allowed, and returns whether anything was deleted.

**Call relations**: It relies on GrantStore._grant_for_actor for the permission check. User or admin surfaces can call this when someone removes an agent’s access to a connected account.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 433–490)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Adds the current agent to an existing connection. It is used when a member wants another agent to use an account that is already connected.

**Data flow**: It receives provider, account ID, conversation ID, actor member ID, and a requested sharing flag. It finds the existing connection, verifies that the actor owns it or it is already shared, refuses attempts to newly share through attach, inserts the grant edge if missing, and returns whether the connection existed.

**Call relations**: This complements GrantStore.record: record lands a new OAuth handoff, while attach reuses an already recorded connection for the current agent.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 492–520)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the underlying connection is shared across the workspace. This is the explicit path for widening or narrowing sharing.

**Data flow**: It receives a grant ID, the desired shared value, and actor member ID. It checks permission through the grant, updates the connection’s shared flag if allowed, and returns whether a row changed.

**Call relations**: It uses GrantStore._grant_for_actor, with stricter admin rules when sharing is being enabled. This keeps sharing changes separate from simple attach operations.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 522–578)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Deletes an entire connected account from the workspace, not just one agent’s grant. It also detaches feed sources and marks their pages as tombstoned so stale synced data is no longer treated as live.

**Data flow**: It receives a connection ID and actor member ID. It verifies permission, finds sources tied to the connection, removes source grants, detaches and marks sources removed, tombstones related pages, deletes the connection row, and returns true if the connection was found and removed.

**Call relations**: It calls GrantStore._connection_for_actor for the ownership/admin check. Because grant edges cascade from the deleted connection, this is the broad cleanup path.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 580–608)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a connection. It returns the connection ID only if the connection exists and the actor is the owner or an allowed admin.

**Data flow**: It receives an open database connection, a connection ID, an actor member ID, and whether admin override is allowed. It locks and reads the connection, checks ownership, optionally checks admin status, and returns the connection ID or raises a permission error.

**Call relations**: GrantStore.disconnect calls it directly. GrantStore._grant_for_actor also uses it when a grant operation must first validate the underlying connection.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 610–620)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Checks whether a workspace member is an administrator. This supports permission decisions for connection changes.

**Data flow**: It receives an open database connection and member ID, reads the member row in the current workspace, and returns true if that member is marked admin.

**Call relations**: GrantStore._connection_for_actor calls this when the actor is not the connection owner and admin override may be allowed.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 622–660)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a grant belongs to the current agent and whether the actor may change the connected account behind it. It prevents someone from editing a grant they cannot control.

**Data flow**: It receives an open database connection, grant ID, actor member ID, and admin rule. It finds the grant’s connection, asks _connection_for_actor to validate access, locks and re-reads the matching grant, and returns the grant ID or nothing.

**Call relations**: GrantStore.revoke and GrantStore.set_shared call this before deleting a grant or changing the connection’s sharing flag.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 681–701)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Starts the OAuth connection flow by producing the provider URL a member should open. It seals the important request details into the state parameter so the callback can later be trusted without a separate pending row.

**Data flow**: It receives workspace, agent, provider, member, conversation, and sharing details. It chooses the provider descriptor, builds a ConnectState object, encrypts it, asks the provider for an authorization URL, and returns that URL.

**Call relations**: ConnectHandoff.authorize calls this when a valid terminal connect request needs a browser link. Later, ConnectFlow.complete opens the sealed state created here.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 703–708)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a provider name is currently connectable. It catches typos or unavailable connectors before a user is sent to a broken flow.

**Data flow**: It receives a provider string. It accepts it if it is in the installed provider map or if the optional resolver claims it; otherwise it raises UnknownProvider.

**Call relations**: This is the fuller validation path for connect requests, especially when an open provider namespace may need an external catalog check.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 710–715)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Performs a quick local check that the connect machinery still has some way to serve a provider. It is deliberately cheaper than full external validation.

**Data flow**: It receives a provider string and returns true if the provider is registered or if an open resolver exists. It does not call external services.

**Call relations**: ConnectHandoff.authorize uses this while holding a database row lock, where it should not do slow catalog validation.


##### `ConnectFlow.bridge_workspace`  (lines 717–723)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to run as. This protects the bridge from mismatched or tampered state.

**Data flow**: It receives a state string, provider string, and callback URL. It opens the sealed state, confirms the provider and callback match, confirms the provider is known, and returns the workspace ID.

**Call relations**: connect_bridge_workspace calls this through the installed flow when a request arrives. If anything does not match, the caller rejects the bridge request.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 725–773)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth handoff after the provider redirects back. It verifies the state, exchanges the code, records the connection and grant, notifies extension hooks, and optionally resumes the waiting conversation.

**Data flow**: It receives the sealed state and provider code. It decrypts and validates the state, gets the provider descriptor, enters the correct workspace and agent context, exchanges the code for an account, records the grant, fires connection hooks, sends a resume message if configured, and returns a GrantRecorded summary.

**Call relations**: This is the landing point for the callback side of the flow. It calls _open, _provider, GrantStore.record, ConnectionHooks.fire, label_for, _resume_key, and ConnectResumption.resume in that order of responsibility.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 775–780)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns the friendly name shown to members for a provider. If no label was declared, it turns the provider slug into readable title words.

**Data flow**: It receives a provider string, looks for it in the labels mapping, and otherwise formats the slug by replacing underscores and title-casing it.

**Call relations**: ConnectFlow.complete uses this when building the success message and the GrantRecorded result.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 782–788)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. It hides the difference between explicitly installed providers and providers served by an open resolver.

**Data flow**: It receives a provider name, checks the provider map, falls back to the resolver if one exists, and returns an OAuthProvider descriptor or raises UnknownProvider.

**Call relations**: ConnectFlow.authorize, ConnectFlow.bridge_workspace, and ConnectFlow.complete all use this before they can ask a provider to build URLs or exchange codes.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 790–795)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. This is what lets the callback trust who requested the connection and what it was for.

**Data flow**: It receives the state token string, asks Fernet to decrypt it with a short time limit, parses it into ConnectState, and returns that object. If the token is expired or changed, it raises ConnectStateInvalid.

**Call relations**: ConnectFlow.bridge_workspace and ConnectFlow.complete call this before trusting any browser-supplied callback data.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 804–886)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a saved terminal connect request into a reusable OAuth URL for the right member. It memoizes the URL so refreshing or retrying the handoff uses the same short-lived authorization state.

**Data flow**: It receives workspace ID, turn ID, and member ID. It locks the turn row, verifies the turn still contains a connect request for that member, checks freshness and provider availability, returns an existing unexpired URL if present, or creates and stores a new URL through ConnectFlow.authorize.

**Call relations**: This is the private handoff layer between a conversation turn and the browser OAuth flow. It calls ConnectFlow.authorize only after validating the stored turn request.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 892–900)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide ConnectFlow used by tools and callback routes. Passing None disables grants and makes later callers fail clearly.

**Data flow**: It receives a ConnectFlow or None and stores it in a module-level variable. It returns nothing.

**Call relations**: Startup code or tests use this to set the one active connect flow. installed_connect_flow later reads the installed value.


##### `installed_connect_flow`  (lines 903–906)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the currently installed ConnectFlow, or raises a clear error if grants are unavailable. This avoids silent failures when no credential key or connect setup exists.

**Data flow**: It reads the module-level installed flow. If present it returns it; if missing it raises ConnectUnavailable.

**Call relations**: connect_bridge_workspace calls this before verifying bridge requests. Other surfaces can also use it as the single access point for the configured flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 909–918)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Extracts and verifies the workspace for a connector browser bridge request. It returns None instead of raising when the request should be rejected.

**Data flow**: It receives a Starlette request, reads state, provider, and callback from query parameters, asks the installed ConnectFlow to verify them, and returns a workspace UUID or None.

**Call relations**: This is a safe wrapper around installed_connect_flow and ConnectFlow.bridge_workspace for request-routing code that needs a yes-or-no workspace answer.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 925–934)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Builds a stable, safe object name for a provider account. It keeps names readable while adding a short hash so similar account IDs do not collide.

**Data flow**: It receives provider and account ID strings. It slugifies both, truncates the readable part to fit the object-name limit, hashes the exact provider/account pair, and returns a combined name.

**Call relations**: It calls _slug for the readable pieces. Other surfaces can use this so connections, grants, and prepared portal intents all refer to the same account edge consistently.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 937–938)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a lowercase dash-separated name fragment. This makes provider and account names safe to use in object names.

**Data flow**: It receives a raw string, lowercases it, replaces runs of non-letter-or-digit characters with dashes, trims extra dashes, and returns the result.

**Call relations**: account_object_name calls this for both provider and account ID before adding the collision-preventing hash.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 941–949)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of connector grants for the current agent. This is useful for showing what the agent can use.

**Data flow**: It reads the current workspace and object-scope agent, builds a database scope for that agent’s grants, delegates the query to _grant_summaries, and returns GrantSummary objects.

**Call relations**: This is the agent-scoped wrapper around the shared _grant_summaries query helper.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 952–955)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of all connector grants in a workspace. This is meant for operator or workspace-wide views.

**Data flow**: It receives a workspace ID, enters that workspace context, delegates a workspace-wide scope to _grant_summaries, and returns GrantSummary objects.

**Call relations**: This is the workspace-wide wrapper around _grant_summaries, unlike grant_summaries which narrows to the current agent.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 958–998)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant and connection rows into human-readable grant summaries. It joins in agent names so the result can be shown in audit views.

**Data flow**: It receives a database filter describing which grants to include. It queries grants joined to connections and agents, orders them by provider and agent name, and returns GrantSummary objects.

**Call relations**: grant_summaries and workspace_grant_summaries both call this to avoid duplicating the same summary-building query.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 3 external calls (__init__, select, workspace_tx).


##### `connection_summaries`  (lines 1001–1063)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the workspace’s connected accounts and the agents currently granted each one. This gives a connection-first view rather than a grant-first view.

**Data flow**: It reads connection rows in the current workspace, left-joins any grants and agent names, groups rows by provider and account ID, gathers agent names for each connection, and returns ConnectionSummary objects.

**Call relations**: User or admin surfaces can call this when they need to show connected accounts and who can use them.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1066–1101)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. Feed registration can use this to know which member-connected accounts the main agent may sync.

**Data flow**: It reads the current workspace, joins connections to grants and agents, filters to the agent marked as main, orders by provider and account ID, and returns MainAgentConnection objects.

**Call relations**: Feed-related code can call this to discover accounts available to the main agent, while accounts granted only to shipped or specialized agents stay out of this list.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

This file exists for one special moment: a user has started connecting an outside account, such as through a CLI or a Slack conversation, and their browser is redirected back after they give consent. At that point there is no normal logged-in web session. The only trusted information is the sealed OAuth state that was created earlier and sent through the provider. OAuth is the common “let this app access my account” flow used by many services.

The main route, `/v1/connect/callback`, checks that the browser brought back both the state and the provider’s temporary code. It asks the grants system for the installed connection flow, verifies the state, exchanges the code, and records the resulting grant. Then it returns a tiny plain HTML page saying the account is connected. If the original conversation was successfully resumed, the page tells the user they can close it. If not, it tells them to go back and ask the agent to continue.

The second route, `/v1/connect/logo.svg`, serves the logo for that page. The page is deliberately very small: no JavaScript, no font download, no stylesheet file. This matters because the user may be on a phone or in a one-off browser tab, and the only purpose is to confirm what happened and send them back.

#### Function details

##### `connect_callback`  (lines 61–88)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: This is the HTTP endpoint the OAuth provider redirects the browser to after the user approves a connection. It verifies the returned state and code, finishes the account connection, and shows the user a simple confirmation page.

**Data flow**: The browser sends in two query values: `state`, which proves which conversation and user started the connection, and `code`, which is the provider’s temporary approval token. The function first gets the installed connection flow, rejects the request if the feature is unavailable or the browser did not send both values, then asks the flow to complete the connection. If the state is bad, the provider is unknown, or required data is missing, it turns that into a clear HTTP error. If completion succeeds, it builds a short account name, escapes it so it is safe to place into HTML, and returns a small confirmation page telling the user what to do next.

**Call relations**: FastAPI calls this function when a request reaches the `/v1/connect/callback` route. The function hands the real OAuth completion work to `ufo.grants.installed_connect_flow()` and the flow object it returns, then uses FastAPI’s `HTTPException` for error responses and `HTMLResponse` for the final browser page. It uses `html.escape` right before rendering so provider or account names cannot accidentally become page markup.

*Call graph*: 4 external calls (HTTPException, HTMLResponse, escape, installed_connect_flow).


##### `connect_logo`  (lines 92–100)

```
async def connect_logo() -> Response
```

**Purpose**: This is the HTTP endpoint that returns the UFO logo shown on the OAuth confirmation page. It exists because the callback page is served by this backend directly and cannot rely on the normal frontend build assets.

**Data flow**: A browser requests the logo path. The function reads the SVG logo file from disk, wraps those bytes in an HTTP response, labels it as an SVG image, and adds a long cache header so browsers can reuse it for a long time without asking again.

**Call relations**: FastAPI calls this function when the confirmation page’s image tag asks for `/v1/connect/logo.svg`. The function does not involve the OAuth flow; it simply serves the static logo through FastAPI’s `Response` so the callback page can stay small while still displaying the project mark.

*Call graph*: 1 external calls (Response).


### Verified account binding flows
Specialized extension flows prove ownership or access before binding GitHub App installations or iMessage phone numbers to a workspace member.

### `extensions/coding/ufo_ext_coding/connect.py`

`orchestration` · `GitHub connection setup`

This file is the safety gate for hooking a workspace up to GitHub. A GitHub App installation id is just a small number, so the system must not trust it by itself. The file uses GitHub’s own authorization flow to prove that the person returning from GitHub can actually see the installation they are trying to connect.

The flow has two halves. First, `connect_github` gives a workspace admin a GitHub installation link. That link includes a sealed piece of state, like a tamper-proof claim ticket, saying which workspace and credential slot this setup belongs to. GitHub sends the browser back to this extension after installation.

Second, `github_installed` receives that return request. It checks that GitHub provided both an authorization code and an installation id. Then `GitHubInstallExchange.reaches` trades the code for the user’s own GitHub token and asks GitHub which ufo installations that user can access. Only if the claimed installation appears in GitHub’s answer does the workspace bind it as a credential.

The important behavior is that the installation id from the URL is treated only as a candidate. GitHub’s account of the signed-in user’s access is the final proof.

#### Function details

##### `connect_github`  (lines 48–72)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: Starts the GitHub connection process for a workspace admin. It creates a one-use installation link that sends the admin to GitHub and carries sealed workspace information for the return trip.

**Data flow**: It receives the current tool context and a short user-facing description. It checks that the speaker is a workspace admin, checks that this deployment has a GitHub App configured, asks the context to create sealed authorization state for the GitHub installation slot, and returns a message containing the GitHub install URL with that sealed state attached.

**Call relations**: This is the outward-facing tool call that begins the flow. It calls the tool context to confirm admin permission and create the sealed state, asks the manifest for the GitHub App id, then packages the result as text for the user. GitHub later returns to the callback handled by `github_installed`.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 75–80)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: Finds which workspace a GitHub return request belongs to by reading the sealed state in the request. It is a small helper for turning a browser redirect back into a workspace identity.

**Data flow**: It receives an HTTP request, reads the `state` query parameter, and asks the credential system to verify and open that sealed value for the expected credential slot and purpose. It returns the workspace UUID if the state is valid, or nothing if it is missing or invalid.

**Call relations**: This helper relies on `authorized_slot_workspace` to do the real seal-checking. It fits into the return-leg routing story: the browser request has no live conversation attached, so the sealed state is how the system can know which workspace the request is for.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 99–126)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: Checks with GitHub whether the user who just authorized can actually access the claimed GitHub App installation. This is the key security check that prevents binding someone else’s organization by guessing or pasting an id.

**Data flow**: It receives a GitHub authorization code and a claimed installation id. It sends the code, client id, and client secret to GitHub to get a user access token. If GitHub does not return a token, it raises a GitHub authorization error. With the token, it asks GitHub for the installations visible to that user, filters them to this app, and returns true only if the claimed installation id is among them.

**Call relations**: This method is used during the callback flow started by GitHub’s redirect. `github_installed` gets an exchange object from `install_exchange`, then calls this method to turn GitHub’s authorization code into proof of access before binding anything to the workspace.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 129–140)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: Builds the object that can talk to GitHub during the installation callback. It gathers this deployment’s GitHub App identity from configuration and environment variables.

**Data flow**: It reads the GitHub App id from the extension manifest and the app client id and secret from environment variables. If no app id is configured, it stops with an error. Otherwise it returns a `GitHubInstallExchange` ready to verify an installation through GitHub.

**Call relations**: `github_installed` calls this when it needs to validate the installation returned by GitHub. This function keeps configuration lookup separate from the verification work done by `GitHubInstallExchange.reaches`, which also makes tests easier to substitute.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 143–172)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Finishes the GitHub installation flow after GitHub redirects the user back. It validates the returned code and installation id, checks the user’s real GitHub access, and binds the installation to the workspace only if the proof succeeds.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error page. Otherwise it creates an install exchange, asks GitHub whether the user reaches that installation, and handles failure cases with clear pages. If the check passes, it writes the installation id into the workspace credential slot and returns a success page.

**Call relations**: This is the return-leg controller for the connection process begun by `connect_github`. It calls `install_exchange` to get the GitHub verifier, calls `_page` to format all browser responses, and uses the context’s credentials object to save the installation once GitHub has proven it is valid.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 175–184)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: Creates a simple HTML response page for the browser after the GitHub callback. It gives the user a readable success or error message instead of raw API text.

**Data flow**: It receives a message and an HTTP status code. It wraps the message in a small HTML document and returns a response with that status and `text/html` content.

**Call relations**: `github_installed` calls this for every visible outcome: missing authorization data, GitHub rejecting the code, forbidden installation access, and successful connection. It is the final presentation step after the callback logic decides what happened.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).


### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file is the “connect my phone” doorway for the iMessage extension. Its job is to take a phone number from a member, make sure it is shaped like a real international phone number, connect the workspace to the configured iMessage provider if needed, and then start a short-lived confirmation claim. Without this file, users could not reliably prove that a phone number belongs to them before the system starts using it for iMessage.

The flow is deliberately cautious. First, the input model cleans and checks the phone number. Then the main tool, ImessageConnect.run, confirms that the requester is a signed-in member and that an iMessage provider is available. If the workspace has not yet been bound to that provider, only an admin is allowed to make that binding.

Next, the tool looks in shared storage for an existing pending claim for the phone number. A pending claim is like a temporary reservation ticket: it says which member is trying to connect the number, where to send the confirmation message, and when the ticket expires. If the same member asks again before it expires, the tool resends the confirmation text. If another member has the ticket, the tool refuses.

A special case matters: some messaging lines cannot send to a phone until that phone texts them first. When that happens, the tool returns instructions and a ready-made SMS link so the user can send the required opt-in message.

#### Function details

##### `opt_in_link`  (lines 29–33)

```
def opt_in_link(assigned_phone_number: str) -> str
```

**Purpose**: Builds a phone link that opens the Messages app with the required opt-in text already filled in. This makes it easier for the user to send the first message to the assigned iMessage line.

**Data flow**: It receives the assigned phone number for the shared iMessage line. It safely encodes the standard opt-in message so it can be placed inside a link, then returns an sms: link pointing at that number with the message body prepared.

**Call relations**: When the system learns that the target phone has not opted in yet, _opt_in_result calls this helper to include a convenient link in the tool response.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `ImessageConnectInput._e164`  (lines 46–50)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Checks that the phone number supplied to the tool is in E.164 format, the common international form like +14155550123. This prevents the rest of the connection flow from working with vague or local-only phone numbers.

**Data flow**: It receives the phone number text from the user input. It removes whitespace, compares the result to the expected international phone-number pattern, and either returns the cleaned phone number or raises an error explaining that the format is invalid.

**Call relations**: This validator runs as part of building ImessageConnectInput, before ImessageConnect.run starts the connection process. It acts as the front-door check so later provider calls receive a predictable phone number.


##### `_result`  (lines 53–59)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Creates the standard response format returned by this tool. It packages a connection state, a human instruction, and any extra details into a JSON text result.

**Data flow**: It receives a state such as pending or not_connected, an instruction for the user, and optional extra fields. It turns these into a JSON object, wraps that JSON as text content, and returns a ToolResult marked as untrusted content.

**Call relations**: ImessageConnect.run uses this helper whenever it needs to answer the caller, such as when a user is not signed in, an admin is required, or confirmation is pending. _opt_in_result also uses it so opt-in replies have the same shape as other replies.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 62–70)

```
def _opt_in_result(assigned_phone_number: str) -> ToolResult
```

**Purpose**: Builds the specific response used when the iMessage line is not allowed to message the user’s phone yet. It tells the user to send the opt-in text first, then try the connection again.

**Data flow**: It receives the assigned iMessage phone number. It creates a not_connected result containing the assigned number, the exact text the user must send, and an SMS link that pre-fills that text.

**Call relations**: ImessageConnect.run calls this when the message provider refuses because the phone has not opted in. This helper delegates the common response packaging to _result and asks opt_in_link to create the convenient Messages link.

*Call graph*: calls 2 internal fn (_result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 77–151)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full phone-number connection process for the iMessage extension. It verifies who is asking, ensures the workspace is connected to the provider, creates or reuses a temporary phone claim, and sends the confirmation text.

**Data flow**: It receives the tool context, which contains the current member, workspace extension services, storage, and idempotency information, plus validated input containing the phone number. It checks that the requester is signed in, loads the provider, binds the workspace to that provider when an admin is allowed to do so, reads or writes the pending phone claim in storage, and asks the provider to register the phone and send the confirmation text. It returns a ToolResult telling the caller whether the connection is pending, blocked, or requires opt-in first; it may also update extension installation binding and shared claim storage.

**Call relations**: This is the main function callers use when a member asks to connect iMessage. It calls _result for normal user-facing replies, _opt_in_result for the special first-message-required path, checks admin status through the tool context, uses phone_key and PendingClaim to store the temporary claim, and calls provider methods to register the phone and send confirmation messages.

*Call graph*: calls 3 internal fn (speaker_is_admin, _opt_in_result, _result); 5 external calls (__init__, now, model_validate, phone_key, uuid4).

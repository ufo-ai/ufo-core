# External account grants and connector authorization  `stage-18.4`

This stage is shared behind-the-scenes support for deciding which outside services an agent is allowed to use. It is like the permission desk for accounts such as GitHub or API-based source providers.

The grants code lets a workspace member connect an outside account using OAuth, a standard “sign in and approve access” flow. It records who owns the connection, which agents may use it, how the connection appears in the workspace, and what happens when it is shared, revoked, or cleaned up.

The GitHub connection code is for workspace administrators. When an admin links a GitHub App installation, it checks that the installation really belongs to the GitHub user who approved it, rather than trusting a raw installation number from a web link.

The GitHub App token code then turns that approved installation into short-lived GitHub access tokens. It prefers the workspace’s organization-approved GitHub identity, and uses a member’s personal token only if no App installation is available.

The direct source connector code covers services that use an API key. It retrieves the stored key safely and turns it into a bearer credential for provider calls.

## Files in this stage

### Workspace account grants
Defines how members connect external OAuth accounts and grant, display, revoke, or clean up agent access to those connections.

### `core/src/ufo/grants.py`

`domain_logic` · `OAuth request handling, grant changes, and connection listing`

This file is the project’s “connected accounts” control center. Imagine a member connects a Google, Slack, or other provider account, then says which agent may use it. The outside account’s real secret token stays on the server-side broker. The agent only gets a safe marker that can be recognized later, so secrets do not leak into the sandbox.

The main flow has two halves. `ConnectFlow.authorize` creates an OAuth consent link, sealing the workspace, member, agent, provider, and conversation into a short-lived encrypted `state` value. Later, `ConnectFlow.complete` opens that state, exchanges the returned code with the provider, records or reuses the connection, grants the target agent access, and notifies extension hooks that a connection has landed.

`GrantStore` is the database-facing part. It creates connection rows, creates agent-to-connection grant rows, lists active grants, revokes grants, changes sharing, attaches existing shared connections, and disconnects a connection completely. It also enforces important safety rules: one provider account cannot silently move from one member to another, private connections cannot be attached by other members, and destructive actions require ownership or allowed admin authority.

The rest of the file provides small supporting pieces: provider interfaces, summary views for user/operator screens, stable object names, and a process-wide installed connect flow used by tools and callback routes.

#### Function details

##### `grant_sentinel`  (lines 40–44)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds the special placeholder credential string for a connected account. This lets the sandbox and the egress proxy agree which server-side connection should be used without passing the real secret around.

**Data flow**: It takes an account id, prefixes it with a fixed sentinel label, and returns the combined string. Nothing is stored or changed.

**Call relations**: This is a small shared convention: any code that exports grant credentials and any code that recognizes them can independently produce the same marker from the same account id.


##### `OAuthProvider.provider`  (lines 91–91)

```
def provider(self) -> str
```

**Purpose**: Names the provider that this OAuth connector represents, such as a provider slug used in records and URLs.

**Data flow**: An implementation exposes a provider name. Callers read that name and use it as the stable label when recording or returning connection information.

**Call relations**: Provider implementations supply this property so `ConnectFlow` and `GrantStore` can record the completed connection under the correct provider identity.


##### `OAuthProvider.host`  (lines 94–94)

```
def host(self) -> str
```

**Purpose**: Gives the provider host that the grant should allow through outbound network access. The host is the network destination tied to the connected account.

**Data flow**: An implementation exposes a host string. The completed connect flow reads it and stores it with the connection grant.

**Call relations**: When `ConnectFlow.complete` records a connection, this value travels into `GrantStore.record` so later egress checks know what host belongs to the grant.


##### `OAuthProvider.authorize_url`  (lines 96–96)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the browser URL where a member gives consent to the outside provider. Someone uses it when starting a connection flow.

**Data flow**: It receives sealed state and a callback URL, combines them with provider-specific OAuth details, and returns a URL for the member to open.

**Call relations**: `ConnectFlow.authorize` asks the selected provider for this URL after it has prepared the encrypted state that must come back on the callback.


##### `OAuthProvider.exchange`  (lines 98–100)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the provider’s returned authorization code into the project’s stable connected-account identity. The real token stays with the broker or provider-side implementation.

**Data flow**: It receives the short code from the callback, the redirect URL, the workspace id, and the original state. It verifies and exchanges those details with the provider, then returns an `OAuthAccount` containing the account id and optional label.

**Call relations**: `ConnectFlow.complete` calls this after reopening the sealed state, then passes the returned account identity to `GrantStore.record`.


##### `OAuthProviderResolver.claims`  (lines 111–111)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open-ended provider resolver can serve a provider name. This is used when providers are discovered dynamically instead of being listed one by one.

**Data flow**: It receives a provider slug, performs whatever catalog check the resolver needs, and returns true if that provider is available.

**Call relations**: `ConnectFlow.validate_provider` uses this when a provider is not in the fixed provider map, so bad provider names fail before a dead consent link is created.


##### `OAuthProviderResolver.descriptor`  (lines 113–113)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuth provider descriptor for a dynamically resolved provider name.

**Data flow**: It receives a provider slug and returns an `OAuthProvider` object that knows how to authorize and exchange for that provider.

**Call relations**: `ConnectFlow._provider` falls back to this resolver when the provider is not in the installed fixed map.


##### `ConnectionHooks.fire`  (lines 202–202)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Notifies installed extensions that a new connection has been recorded. Extensions can then create the extra state, such as feed source rows, that follows from a connected account.

**Data flow**: It receives a `ConnectionRecorded` payload describing the new or reused connection and performs extension-defined follow-up work. It returns no data to this file.

**Call relations**: `ConnectFlow.complete` calls this after the connection has been recorded, if hook support was installed.


##### `GrantStore.workspace_id`  (lines 224–225)

```
def workspace_id(self) -> UUID
```

**Purpose**: Reads the workspace id that database operations should apply to. This keeps grant work scoped to the currently active workspace.

**Data flow**: It reads the current workspace context through `ws_current()` and returns its workspace id.

**Call relations**: Most `GrantStore` methods rely on this property before reading or writing grant-related rows, so operations do not accidentally cross workspace boundaries.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 228–229)

```
def agent_id(self) -> UUID
```

**Purpose**: Reads the agent id that grant operations should target. This supports object-dispatch behavior, where a command may be acting on a specific agent.

**Data flow**: It asks `object_agent_id()` for the active agent id and returns it.

**Call relations**: `GrantStore.record`, `active_grants`, `attach`, and permission checks use this value to create or find grant edges for the intended agent.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 231–326)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None) -> UUID
```

**Purpose**: Creates or reuses a member-owned connection and grants the current agent access to it. It is the durable database landing step after OAuth succeeds.

**Data flow**: It receives provider/account details, owner member, conversation, sharing choice, host, and optional account label. It rejects unsafe account ids with control characters, inserts the connection if missing, verifies the same member owns any existing connection, updates host/sharing/label, upserts the agent grant row, and returns the connection id.

**Call relations**: `ConnectFlow.complete` calls this after the provider exchange returns an account. It uses a workspace transaction and database upsert behavior, and raises `ConnectionOwnedByAnotherMember` if a different member already owns that provider account in the workspace.

*Call graph*: 7 external calls (__init__, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 328–365)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the current agent’s usable connection grants. This is how code finds which connected accounts an agent may use.

**Data flow**: It reads grant rows joined with connection rows for the current workspace and agent, converts each row into a `Grant`, and returns them as a tuple.

**Call relations**: The sandbox environment builder `ufo/sandbox/exec_env._grant_cli_env` calls this to know which grant markers should be made available to an agent process.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 367–379)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, after checking the acting member is allowed to affect the underlying connection.

**Data flow**: It receives a grant id and actor member id. It looks up and locks the grant through `_grant_for_actor`; if unavailable it returns false, otherwise it deletes the grant row and returns whether a row was deleted.

**Call relations**: It delegates permission and grant lookup to `_grant_for_actor`, then performs the deletion inside a workspace transaction.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 381–438)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Gives the current agent access to an existing connection. This is only allowed if the actor owns the connection or the connection is already shared across the workspace.

**Data flow**: It receives provider, account id, conversation id, actor member id, and a requested sharing flag. It finds the connection, checks ownership or sharing, refuses attempts to make a private connection shared through attach, inserts the grant if missing, and returns true. If the connection does not exist, it returns false.

**Call relations**: This method is used when no new OAuth exchange is needed because the connection already exists. It enforces access rules directly before inserting the grant row.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 440–468)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the connection behind a grant is shared with the workspace. This is the controlled path for widening or narrowing sharing.

**Data flow**: It receives a grant id, desired shared value, and actor member id. It checks that the actor can mutate the grant via `_grant_for_actor`, updates the underlying connection’s shared flag, and returns whether the update happened.

**Call relations**: It relies on `_grant_for_actor` for permission checks. Admin authority is only allowed when narrowing sharing, matching the file’s rule that attach should not secretly widen someone else’s private access.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 470–526)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a connection and cleans up the feed data tied to it. This is stronger than revoking one agent’s grant.

**Data flow**: It receives a connection id and actor member id. It checks ownership or admin permission, finds sources attached to the connection, removes source grants, detaches and marks those sources removed, tombstones their pages, deletes the connection row, and returns true if the connection was found and removed.

**Call relations**: It calls `_connection_for_actor` for the permission check, then performs all cleanup in one workspace transaction so dependent rows are not left half-updated.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 528–556)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a specific connection. It is an internal guard used before changing or deleting connection-related data.

**Data flow**: It receives an open database connection, a connection id, an actor member id, and whether admin override is allowed. It locks and reads the connection, returns none if missing, returns the id if the actor owns it, otherwise checks admin status and either returns the id or raises a permission error.

**Call relations**: `GrantStore.disconnect` calls this for whole-connection actions, and `_grant_for_actor` calls it when a grant action needs to verify the underlying connection owner.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 558–568)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Answers whether a member is an admin in the current workspace. It is a small helper for permission decisions.

**Data flow**: It receives an open database connection and a member id. It reads the member’s `is_admin` flag for the current workspace and returns true or false.

**Call relations**: `_connection_for_actor` calls this only after the actor is not the connection owner and admin override might matter.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 570–608)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a specific grant for the current agent. It protects revoke and sharing changes from acting on the wrong agent or an unauthorized connection.

**Data flow**: It receives an open database connection, grant id, actor member id, and admin option. It finds the grant’s connection for the current workspace and agent, asks `_connection_for_actor` to verify permission, then locks and returns the grant id if it still matches.

**Call relations**: `GrantStore.revoke` and `GrantStore.set_shared` call this before changing grant or connection state.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 627–647)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Starts an OAuth connection by producing the provider consent URL. It seals the important request details so the callback can later be trusted without storing a pending server row.

**Data flow**: It receives workspace, agent, provider, grantor member, conversation, and sharing information. It finds the provider descriptor, packages those details into `ConnectState`, encrypts that state with Fernet, and returns the provider’s authorization URL.

**Call relations**: Connect surfaces and `ConnectHandoff.authorize` use this to create the browser URL that the member opens.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 649–654)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks that a provider name is available before a connect request is accepted. This prevents creating links for providers the system cannot actually serve.

**Data flow**: It receives a provider slug. If it is in the installed provider map, it succeeds; otherwise it asks the resolver, if present, whether it claims the provider; if neither path works, it raises `UnknownProvider`.

**Call relations**: This is the more thorough provider check that may call a resolver’s live catalog before a request is stored or presented.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 656–661)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether this process appears able to serve a provider. It avoids slower resolver validation when reusing an already-created connect request.

**Data flow**: It receives a provider slug and returns true if the provider is explicitly installed or if an open resolver exists at all.

**Call relations**: `ConnectHandoff.authorize` uses this while holding a turn-row lock to ensure the provider has not disappeared before minting or returning a memoized URL.


##### `ConnectFlow.bridge_workspace`  (lines 663–669)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to operate in. A bridge request is accepted only if its visible parameters match the encrypted state.

**Data flow**: It receives encrypted state, provider, and callback URL. It opens the state, compares the provider and callback against trusted values, verifies the provider exists, and returns the workspace id. If anything does not match, it raises `ConnectStateInvalid` or `UnknownProvider`.

**Call relations**: `connect_bridge_workspace` calls this through the installed connect flow when handling connector bridge requests.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 671–700)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth handoff after the provider redirects back with a code. It records the connected account, grants the intended agent, and notifies extension hooks.

**Data flow**: It receives encrypted state and an OAuth code. It opens the state, finds the provider, enters the sealed workspace and agent context, exchanges the code for an account, records the connection and grant through `GrantStore.record`, fires `ConnectionHooks.fire` if installed, and returns a `GrantRecorded` summary.

**Call relations**: This is the callback-side partner to `authorize`. It hands provider exchange results to `GrantStore`, and then hands a committed connection description to extensions through `ConnectionRecorded`.

*Call graph*: calls 2 internal fn (_open, _provider); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow._provider`  (lines 702–708)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the provider descriptor for a provider name. It hides the two ways providers can be supplied: fixed map or open resolver.

**Data flow**: It receives a provider name, returns the matching installed descriptor if present, otherwise asks the resolver for one if available, and raises `UnknownProvider` if neither exists.

**Call relations**: `ConnectFlow.authorize`, `bridge_workspace`, and `complete` all call this before provider-specific work.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 710–715)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the short-lived OAuth state value. This protects callbacks from tampered or stale browser data.

**Data flow**: It receives the encrypted state string, decrypts it with a time-to-live limit, parses it as `ConnectState`, and returns the claims. If decryption fails or the state is expired, it raises `ConnectStateInvalid`.

**Call relations**: `ConnectFlow.bridge_workspace` and `ConnectFlow.complete` call this before trusting any workspace, provider, member, or agent information from the browser.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 724–806)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a stored terminal connect request into one reusable OAuth URL for the requesting member. It prevents repeated clicks or races from minting different links for the same request.

**Data flow**: It receives workspace id, turn id, and member id. It locks the turn row, verifies the terminal frame still contains a connect request for that member, checks the provider and time limits, returns a still-valid memoized URL if present, or creates a new URL through `ConnectFlow.authorize`, stores it on the turn, and returns it.

**Call relations**: This method sits between a user-facing connect surface and `ConnectFlow.authorize`. It reads the terminal request record, enforces freshness and ownership, then either reuses or records the generated authorization URL.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 812–820)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow object. This gives tools and callback handlers a shared way to find the configured OAuth machinery.

**Data flow**: It receives a `ConnectFlow` or none and stores it in the module-level `_installed_flow` variable. It returns nothing.

**Call relations**: Startup code installs the flow before requests run. Tests can also replace it with a stub. Later, `installed_connect_flow` reads this value.


##### `installed_connect_flow`  (lines 823–826)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed connect flow or fails loudly if grants are not configured. This avoids silent behavior when the deployment has no credential key.

**Data flow**: It reads the module-level `_installed_flow`. If a flow exists it returns it; otherwise it raises `ConnectUnavailable`.

**Call relations**: `connect_bridge_workspace` calls this before verifying bridge requests. Other parts of the system can use it as the single access point for the configured flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 829–838)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Safely extracts the workspace from a connector bridge HTTP request. It returns none instead of raising when the request is not valid.

**Data flow**: It reads `state`, `provider`, and `callback` query parameters from the request, asks the installed connect flow to verify them, and returns the workspace id. If the flow is unavailable, the provider is unknown, or the state is invalid, it returns null.

**Call relations**: This is a request-facing wrapper around `installed_connect_flow().bridge_workspace`, shaped for route code that wants a simple accept-or-reject answer.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 845–854)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. It keeps names short while still distinguishing accounts that would otherwise look the same after cleanup.

**Data flow**: It receives provider and account id. It slugs both into lowercase dash-separated text, hashes the original provider/account pair, truncates the readable head to leave room for the hash, and returns the final name.

**Call relations**: It calls `_slug` for the readable pieces and `hashlib.sha256` for the digest. Other surfaces can use the same function so a connection and its grant are named consistently.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 857–858)

```
def _slug(raw: str) -> str
```

**Purpose**: Converts arbitrary text into a simple lowercase slug for names. A slug is a URL- and object-name-friendly string made of words separated by dashes.

**Data flow**: It receives raw text, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and returns the cleaned string.

**Call relations**: `account_object_name` calls this for both provider names and account ids before adding the hash suffix.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 861–869)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of the current agent’s connector grants. This is useful for showing what accounts the agent can currently use.

**Data flow**: It reads the current workspace and object agent id, builds a filter for that scope, asks `_grant_summaries` to run the joined query, and returns the resulting summaries.

**Call relations**: This is the agent-scoped wrapper around `_grant_summaries`; it supplies the current-agent filter and lets the shared helper do the database work.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 872–875)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of all connector grants in one workspace. This is meant for broader operator or workspace-level views.

**Data flow**: It receives a workspace id, temporarily enters that workspace context, asks `_grant_summaries` for all grant rows in that workspace, and returns the summaries.

**Call relations**: This is the workspace-wide wrapper around `_grant_summaries`, unlike `grant_summaries`, which limits results to one current agent.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 878–918)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into human-readable grant summaries.

**Data flow**: It receives a SQL filter describing the desired scope. It joins connector grants with connections and agents, orders by provider and agent name, converts each row into a `GrantSummary`, and returns a tuple.

**Call relations**: `grant_summaries` and `workspace_grant_summaries` both call this with different filters so the query logic stays in one place.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 3 external calls (__init__, select, workspace_tx).


##### `connection_summaries`  (lines 921–983)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists this workspace’s connected accounts and the agents currently granted each one. It shows connections from the owner-account point of view rather than just one agent’s grants.

**Data flow**: It reads connection rows for the current workspace, left-joins any grant and agent names, groups rows by provider and account id, collects agent names, and returns `ConnectionSummary` objects.

**Call relations**: This function is used by views that need a workspace inventory of connections, including connections that may have zero, one, or many agent grants.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 986–1021)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. Feed registration can use this to know which member-owned accounts the main agent may sync.

**Data flow**: It reads current-workspace connections joined through grants to agents marked as the main agent, orders them by provider and account id, converts rows into `MainAgentConnection` objects, and returns them.

**Call relations**: This is narrower than `connection_summaries`: it only reports accounts the main agent can use, so accounts connected only for a shipped or specialized agent are intentionally absent.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### GitHub App authorization
Binds a workspace to a verified GitHub App installation and selects the correct short-lived GitHub token for authorized work.

### `extensions/coding/ufo_ext_coding/connect.py`

`orchestration` · `GitHub connection setup and redirect request handling`

This file is the safety gate for connecting GitHub to a workspace. A GitHub App installation id is just a number, so by itself it is not proof that the workspace should be allowed to use that installation. Without this file’s checks, someone could try to bind a workspace to the wrong organization’s GitHub installation.

The flow has two halves. First, an admin asks ufo to connect GitHub. The file creates a GitHub installation link with a sealed state value attached. That sealed state is like a tamper-proof ticket: it says which workspace and credential slot this connection is for, and GitHub sends it back after installation.

Second, GitHub redirects the browser back after the admin installs or authorizes the App. The file reads GitHub’s authorization code and the claimed installation id. It then asks GitHub directly: “using this person’s own GitHub authorization, which ufo App installations can they reach?” Only if the claimed installation appears in GitHub’s answer does the file bind that installation to the workspace.

The important idea is that the URL parameter is only a selector, not trusted proof. GitHub’s own account of the user’s reachable installations is the proof.

#### Function details

##### `connect_github`  (lines 48–72)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: This starts the GitHub connection process for a workspace admin. It gives the admin a special GitHub App installation link that carries a short-lived, sealed state value tying the future browser return to this workspace.

**Data flow**: It receives the current tool context and a short user-facing description. It checks that the speaker is a workspace admin, checks that this deployment has a GitHub App configured, asks the credential system to create a sealed authorization state for the GitHub installation slot, and returns a message containing the GitHub installation URL. If the user is not an admin or the App is not configured, it stops with an error instead of producing a link.

**Call relations**: This is the first half of the connection story. It calls the tool context to confirm admin permission and to create the sealed credential authorization. It also asks the manifest for the GitHub App id so it knows the deployment is ready. The link it returns eventually leads GitHub back to the route handled by github_installed.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 75–80)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: This reads the sealed state that came back from GitHub and works out which workspace the installation return belongs to. It is used when the browser redirect does not otherwise carry conversation context.

**Data flow**: It receives an HTTP request, reads the state query parameter, and passes that state to the credential helper along with the expected slot name and payload purpose. If the state is valid and matches this GitHub installation flow, it returns the workspace id. If not, it returns nothing.

**Call relations**: This function relies on authorized_slot_workspace to verify the sealed state instead of trusting raw request data. It supports the redirect side of the flow, where the system must recover the workspace from the tamper-proof state value created earlier by connect_github.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 99–126)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: This asks GitHub whether the GitHub user who just authorized the return can actually access the claimed App installation. It is the core security check that prevents binding a workspace to an installation id the user does not control.

**Data flow**: It receives a GitHub authorization code and an installation id. It exchanges the code with GitHub for a user access token, then uses that token to request the list of GitHub App installations visible to that user. It filters the list to this deployment’s App id and returns true only if the claimed installation id is present. If GitHub does not return an access token, it raises an authorization error.

**Call relations**: github_installed uses this method during the redirect return before saving anything. The method uses an HTTP client to talk to GitHub’s token endpoint and installations endpoint. If the token exchange fails, it signals that with GitHubAuthorizationError so github_installed can show an appropriate failure page.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 129–140)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: This builds the object that knows how to perform the GitHub authorization-code exchange for this deployment. It gathers the GitHub App identity from configuration and environment variables.

**Data flow**: It reads the configured GitHub App id and the OAuth client id and client secret from environment-backed settings. If no GitHub App id is configured, it raises an error. Otherwise, it returns a GitHubInstallExchange containing the credentials needed to ask GitHub about the authorizing user’s installations.

**Call relations**: github_installed calls this when GitHub redirects back. Keeping this setup in its own function means the redirect handler can simply ask for an exchange object, and tests can replace or control the configuration more easily.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 143–172)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This handles GitHub’s browser redirect after the App is installed or authorized. It verifies the returned authorization and, only if GitHub confirms the user can reach the claimed installation, saves the installation id into the workspace’s credential slot.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the authorization code and installation id from the query string. If either is missing, it returns an error page. Otherwise it creates a GitHub exchange object, checks whether the authorizing user reaches that installation, and rejects the request if GitHub declines or the installation is not in the user’s list. If the check succeeds, it binds the installation id to the GitHub installation credential slot and returns a success page.

**Call relations**: This is the second half of the flow started by connect_github. It calls install_exchange to get the GitHub checker, then calls _page to turn each outcome into a small browser page. It is deliberately careful not to trust the installation_id parameter until GitHubInstallExchange.reaches has confirmed it.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 175–183)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: This creates a simple HTML response page for the browser after the GitHub redirect. It gives the user a clear success or failure message without needing a larger web template system.

**Data flow**: It receives a message and an HTTP status code. It wraps the message in a small HTML document and returns a Response with that status code and an HTML media type. It does not change stored state.

**Call relations**: github_installed calls this for every visible browser result: missing authorization data, GitHub authorization failure, rejected installation, and successful connection. It keeps the redirect handler’s user-facing responses consistent and compact.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup and token minting during rule derivation or GitHub access setup`

This file solves a safety problem around GitHub access. A GitHub App installation belongs to an organization or account and grants limited permissions. The system must prove that a workspace is allowed to use that installation before asking GitHub for a token. It does that by reading a sealed installation binding from the credential store. “Sealed” means encrypted and tied to the workspace, so someone cannot just type another organization’s installation number and get access.

When a workspace has a valid installation binding, GitHubAppTokens creates a short-lived JSON Web Token, or JWT, which is a signed claim proving this deploy owns the GitHub App. It sends that JWT to GitHub and receives an installation access token. That token is cached until shortly before it expires, so repeated work in the same conversation does not ask GitHub for a new token every time.

If no installation is present, this file deliberately returns nothing so another stored token can be used instead. But if an installation value exists and cannot be opened, it fails rather than falling back. That is important: using a personal token after an organization expected App-based access would mean authenticating as the wrong identity. In everyday terms, it refuses to use someone else’s badge when the company badge is broken.

#### Function details

##### `_segment`  (lines 48–49)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: Turns one part of a JWT into the compact text form GitHub expects. A JWT is made of encoded pieces joined with dots, and this helper prepares one such piece.

**Data flow**: It receives a small dictionary of values, such as the JWT header or body. It converts that dictionary to compact JSON, encodes it with URL-safe base64 text, removes padding characters, and returns the resulting bytes.

**Call relations**: GitHubAppTokens._jwt calls this twice while building the signed JWT: once for the header and once for the body. The output becomes part of the message that is signed with the GitHub App private key.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 73–85)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a workspace has a GitHub App installation bound to it, without actually minting a GitHub token. This is used when the system only needs to know whether App-based GitHub access exists.

**Data flow**: It receives a workspace ID and a credential store. It asks the store for the sealed installation value. If the slot is empty, it returns false. If a value exists, it tries to open it with the workspace-aware seal; if that succeeds, it returns true, and if it cannot be opened, the error is allowed to surface.

**Call relations**: This is the lightweight companion to GitHubAppTokens.secret. Both read and verify the same sealed installation value, so the system gives the same answer when exporting credentials and when actually preparing GitHub access.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 87–111)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns a usable GitHub installation token for a workspace, or returns nothing when the workspace has no App installation. It is careful not to fall back if an installation value exists but is invalid, because that would switch identities silently.

**Data flow**: It receives a workspace ID and credential store. It reads the sealed installation binding, opens it, and uses the workspace plus installation as a cache key. If a still-fresh token is already cached, it returns that token. If not, it starts or joins an in-progress minting task, waits for it safely, and returns the newly minted token.

**Call relations**: This is the main entry point for App-token retrieval inside this file. When a token is missing or near expiry, it hands off to GitHubAppTokens._mint. It uses asyncio task sharing so that two callers asking for the same installation at the same time do not both make separate GitHub requests.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 113–121)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: Mints one installation token and records it in the cache. It also cleans up the “minting in progress” marker when the work is done.

**Data flow**: It receives the cache key and installation ID. It asks GitHubAppTokens._installation_token to get a fresh token and expiry time from GitHub, stores that pair in the minted-token cache, and returns it. Whether the mint succeeds or fails, it removes its own pending task from the in-progress map if it is still the current task.

**Call relations**: GitHubAppTokens.secret creates this as an asynchronous task when there is no fresh cached token. This function is the bridge between the cache-facing logic in secret and the network-facing logic in GitHubAppTokens._installation_token.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 123–158)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: Contacts GitHub to exchange this App’s signed JWT for an installation access token. This is the point where the local proof of App ownership becomes a real GitHub token.

**Data flow**: It receives an installation ID. It creates an HTTP client, sends a POST request to GitHub’s installation-token endpoint with a freshly signed JWT and the requested permissions, and waits for GitHub’s response. If GitHub returns success, it extracts the token and expiry timestamp. If the network fails, GitHub rejects the request, or the response cannot be understood, it raises a credential-minting error.

**Call relations**: GitHubAppTokens._mint calls this when a fresh token is needed. This function calls GitHubAppTokens._jwt to produce the short-lived App authentication proof that GitHub requires before it will issue an installation token.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 3 external calls (__init__, fromisoformat, AsyncClient).


##### `GitHubAppTokens._jwt`  (lines 160–167)

```
def _jwt(self) -> str
```

**Purpose**: Builds and signs the short-lived JWT that proves this server owns the GitHub App registration. GitHub requires this signed claim before it will mint installation tokens.

**Data flow**: It reads the current time, creates a JWT header and body containing the App ID and expiry window, encodes those pieces, signs them with the App’s RSA private key, and returns the final dot-separated JWT string.

**Call relations**: GitHubAppTokens._installation_token calls this immediately before contacting GitHub. It uses _segment to prepare the header and body, then adds the cryptographic signature that lets GitHub trust the request.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `GitHubAPIAuth.bound`  (lines 177–184)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether the workspace has any usable GitHub API authentication: either a GitHub App installation or a stored fallback token. This answers the simple question, “Can this workspace authenticate to the GitHub API?”

**Data flow**: It receives a workspace ID and credential store. If App-token support is configured and the workspace has a valid App installation, it returns true. Otherwise it looks for the fallback credential slot. If that stored token is present, it returns true; if the slot is unset, it returns false.

**Call relations**: This combines the App-token path with the fallback-token path. It relies on the token object’s bound check when available, and otherwise directly asks the credential store whether the fallback slot exists.

*Call graph*: calls 1 internal fn (get).


##### `GitHubAPIAuth.secret`  (lines 186–193)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns the actual HTTP Authorization header value for GitHub API calls. It prefers the GitHub App installation token, but uses the stored fallback token when there is no installation.

**Data flow**: It receives a workspace ID and credential store. It first asks the App-token provider for a token if one is configured. If that returns nothing, it reads the fallback token from the store. If neither exists, it returns nothing. If it finds a token, it prefixes it with “Bearer ”, which is the standard form GitHub expects in the Authorization header.

**Call relations**: This is the final wrapper around whichever credential source wins. It turns the raw token from GitHubAppTokens.secret or the credential store into the string that HTTP code can place on a GitHub API request.

*Call graph*: calls 1 internal fn (get).


##### `app_tokens`  (lines 196–215)

```
def app_tokens(installation_slot: str, permissions: tuple[tuple[str, str], ...] | None=GIT_INSTALLATION_PERMISSIONS) -> GitHubAppTokens
```

**Purpose**: Builds a GitHubAppTokens object from deployment environment variables. It makes the deploy fail loudly if the GitHub App ID or private key is missing or unusable.

**Data flow**: It receives the name of the credential slot that stores installation bindings and optional requested permissions. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the environment, parses the private key as PEM text, checks that it is an RSA private key, and returns a configured GitHubAppTokens instance.

**Call relations**: This is the setup helper for creating the App-token provider. Later, the returned GitHubAppTokens object performs the bound, secret, JWT, and GitHub minting flow when workspace credentials are needed.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### Direct source credentials
Declares the direct API-key path for source connectors that authenticate through member-supplied credentials.

### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `source sync authentication`

Some source connectors need to call an outside provider using an API key that a workspace member added directly. This file is the small bridge that makes that possible without exposing the secret more widely than necessary. The key is stored encrypted under the provider’s name, and the source is routed here through a special direct account handle. When the sync job needs to authenticate, this class reads the right secret through CredentialAccess, which is a controlled way to read only the credential slots the source declared it needs. It then wraps the secret as a bearer credential, meaning the key will be sent like a standard “Bearer ...” token in provider HTTP requests. The important safety rule is that this happens host-side, inside the sync job. The secret is not sent into the sandbox and is not exposed to the agent surface. In everyday terms, this file is like a locked key cabinet clerk: it only opens the one drawer the connector is allowed to use, hands the key directly to the worker making the outside call, and does not leave copies lying around.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method fetches the API key for a provider from the workspace credential store and returns it in the standard Credential shape used by the rest of the auth system. It is used when a direct, bring-your-own-key source needs to authenticate a provider request.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used as the credential slot name, so the method asks CredentialAccess for that stored secret. It then creates and returns a Credential whose bearer token is the fetched secret; it does not modify the account handle or send the secret anywhere else.

**Call relations**: When the direct auth-proxy path needs credentials for a source run, it calls this method to resolve the provider key. The method hands the fetched secret into Credential.__init__ so the rest of the sync code can use a normal bearer credential instead of knowing how the secret was stored.

*Call graph*: 1 external calls (__init__).

# Connection setup and credential vaulting  `stage-12.1`

This stage is the system’s front door for connecting outside accounts and keeping their secrets safe. It runs mostly during setup, before agents can use services like GitHub, Slack, OpenAI, Anthropic, iMessage, or API-key based tools. The grants code records who owns a connected account and which agents may use it. The credentials vault encrypts API keys, checks that secret requests are genuine, and limits where each secret may be used.

Several files act as bridges to outside sign-in services. Composio and Pipedream providers start hosted OAuth flows, where a user approves access on another website. The CLI callback pages finish the redirect back into UFO. The Pipedream client lists allowed connectors and prevents one workspace from using another’s account, while its token helper turns a connected GitHub account into safe sandbox credentials and commit identity.

Other parts cover direct credentials and special apps. Keyed connectors describe API-key services and inject real keys only for approved requests. Source direct auth fetches stored keys for sync jobs. Anthropic and OpenAI login files validate and store personal access. Slack and iMessage tools guide human setup and claim the needed workspace or phone identity.

## Files in this stage

### Hosted account brokers
Bridges UFO connection flows to hosted OAuth brokers, records grants, completes redirects, and shapes connected-account credentials for sandbox use.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `request handling during the connector OAuth connect flow`

OAuth is the common “let this app access my account” web flow. ufo expects to start that flow with a normal authorization URL and later receive a code it can exchange for an account. Composio works differently: ufo must first ask Composio, asynchronously, to create a special consent link. This file solves that mismatch.

The main object, ComposioOAuthProvider, gives ufo something that looks like a normal OAuth provider. Its authorize_url does not go straight to Composio. Instead, it sends the browser to this extension’s own /ext/composio/oauth route. That route can then do the slower work of asking Composio for the real consent link and redirecting the user there.

The same route is used twice, like a reception desk that sends someone out for approval and later receives them back. On the first visit, it creates the Composio consent link. On the return visit, Composio includes a connected_account_id. The route passes that id back to ufo as the OAuth “code.” Finally, exchange checks with Composio that the account belongs to this workspace’s Composio user and wraps it as an OAuthAccount. The actual secret token stays inside Composio, so ufo stores only the connected account identity.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This creates the URL where the user’s browser should go to begin connecting a Composio-backed provider. Instead of sending the browser directly to Composio, it sends it to this extension’s bridge route so the route can create the real Composio consent link.

**Data flow**: It receives the sealed state value and the callback URL that ufo wants to return to. It packages the provider name, state, and callback into query text, extracts the scheme and host from the callback, and builds a URL under /ext/composio/oauth. The result is a browser URL that starts the bridge flow.

**Call relations**: This is the first step of the provider flow. It relies on _origin to make sure the callback has a usable web origin, and on URL encoding so the state and callback survive safely inside the query string. The browser later visits oauth_route with these values.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This turns the returned Composio connected account id into ufo’s OAuthAccount record. It also confirms that the account really belongs to the workspace user and matches this provider before ufo binds it.

**Data flow**: It receives the code value, which in this flow is actually a Composio connected account id, plus the workspace id. It builds the expected Composio user id for that workspace, asks the Composio client for the connected account, and tries to fetch a friendly label for it. It returns an OAuthAccount containing the account id and, if available, the label.

**Call relations**: This runs after oauth_route has handed the connected account id back to ufo’s core callback as the OAuth code. It calls the Composio client to verify the account before returning the account object ufo will bind. If fetching the optional label fails, it still completes the connection with no label.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the HTTP bridge route that covers both halves of the browser consent trip. It starts the Composio consent process, and later receives Composio’s return and forwards the result back to ufo’s normal callback.

**Data flow**: It reads query parameters from the incoming request: state, callback, provider, connected_account_id, and status. If state or callback is missing, it returns a bad-request response. If Composio has returned a connected account id, it redirects to ufo’s callback with that id as the code. If Composio reports a status but no account id, it returns an error page instead of restarting the flow. Otherwise, it treats the request as the start leg: it builds a return URL back to itself, asks Composio for a consent link for this workspace’s user, and redirects the browser to that link.

**Call relations**: The browser reaches this route after ComposioOAuthProvider.authorize_url points it here. On the start leg, the route calls _origin to rebuild a safe public route URL and calls the Composio client to create the hosted consent link. On the return leg, it does not call Composio again; it redirects back to core’s callback so exchange can verify and bind the account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the web origin from a full URL, meaning just the scheme and host such as https://example.com. It also rejects callback URLs that are not usable HTTP or HTTPS web addresses.

**Data flow**: It receives a URL string, parses it into parts, and checks that it has an http or https scheme and a host name. If the URL is valid, it returns only the scheme and host. If not, it raises an error explaining that the OAuth bridge needs a proper callback host.

**Call relations**: Both authorize_url and oauth_route use this helper before building bridge URLs. That keeps their URL-building logic simple and makes sure the flow does not silently create broken redirect links from incomplete or non-web callback values.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `OAuth connection request handling`

OAuth is the common “sign in and allow access” flow used by services like Gmail. UFO expects this flow to start with a simple authorization URL and later finish by exchanging a returned code for an account. Pipedream does not fit that shape exactly: before sending the user to Pipedream’s consent page, UFO must first make an asynchronous API call to mint a Connect token. This file solves that mismatch.

The main idea is a browser bridge. Instead of sending the browser straight to Pipedream, `authorize_url` sends it to UFO’s own `/ext/pipedream/oauth` route. That route checks which connector is being connected, creates a Pipedream Connect token for this specific workspace and connection state, and then redirects the browser to Pipedream’s hosted consent page. When Pipedream sends the browser back, the same route finds the newest account for that exact temporary user and redirects back into UFO’s core flow with the account id as the code.

The provider then exchanges that account id for an `OAuthAccount`. It double-checks that the account belongs to the expected Pipedream app, tries to fetch a friendly label, and, for connectors that need it, records a commit identity used later when the sandbox performs actions. The careful use of state-specific external users prevents two overlapping connection attempts from accidentally picking up each other’s accounts.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 62–64)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first URL the user’s browser should visit when starting a Pipedream-backed connection. Instead of pointing directly at Pipedream, it points at this extension’s local OAuth bridge so the bridge can first create the needed Pipedream token.

**Data flow**: It receives UFO’s sealed connection `state` and the final `redirect_uri` that core expects to return to. It extracts the origin, meaning the scheme and host such as `https://example.com`, then adds the provider name, state, and callback as query parameters. It returns a full URL for `/ext/pipedream/oauth` on that same origin.

**Call relations**: This is the opening move in the flow. It relies on `_origin` to make sure the callback has a usable web origin, and it uses URL encoding so the state and callback can safely ride through the browser. The route it produces is later served by `oauth_route`, which performs the asynchronous Pipedream work that this synchronous method cannot do.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 66–84)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Pipedream account id into UFO’s stored account record. It verifies that the account really belongs to this connector before UFO binds it as a grant.

**Data flow**: It receives the returned `code`, which in this bridge is the Pipedream account id, plus the workspace id and sealed state. From the workspace and state it rebuilds the Pipedream external user id, asks Pipedream for that exact connected account, and checks that the account’s app matches this provider. It then tries to read a friendly account label, optionally reads the commit identity needed by some connectors, and returns an `OAuthAccount` containing the account id, optional label, and optional commit information. If the account belongs to the wrong app, or if required commit identity lookup fails, it stops instead of silently creating a broken connection.

**Call relations**: This runs after `oauth_route` has redirected back to UFO core with the account id. It talks to the Pipedream client to retrieve and verify the account, uses the connector registry to decide whether extra commit identity is needed, and hands the finished `OAuthAccount` back to UFO’s connection system.

*Call graph*: 6 external calls (__init__, get, PipedreamError, connection_user_id, pipedream_client, commit_identity).


##### `oauth_route`  (lines 87–140)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves as the browser bridge for both halves of the Pipedream connection flow. On the way out, it creates a Pipedream Connect token and redirects the user to Pipedream; on the way back, it converts the completed consent into a callback that UFO core understands.

**Data flow**: It reads query parameters from the incoming request: the provider name, sealed state, callback URL, and optionally an outcome marker from Pipedream. If required values are missing, it returns a clear error response. If the provider is unknown, it returns a not-found response. If Pipedream reports a successful connection, it finds the newest account for this workspace-and-state-specific external user and redirects to the core callback with the original state and the account id as the code. If Pipedream reports failure, it returns an error page instead of restarting consent. If this is the starting leg, it creates a Connect token with success and error redirects pointing back to this same route, builds the hosted Pipedream Connect Link, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: This route is reached first from `PipedreamOAuthProvider.authorize_url`. It calls `_origin` to build safe return URLs, consults the connector registry to find the Pipedream app slug, and uses the Pipedream client to create tokens or find the connected account. On success, it hands control back to UFO core by redirecting to the callback, after which `PipedreamOAuthProvider.exchange` verifies and binds the account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 143–147)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the base web origin from a URL, such as `https://example.com`. It also protects the OAuth bridge from using a callback that is not a real HTTP or HTTPS URL.

**Data flow**: It receives a URL string, parses it into pieces, and checks that it has both a web scheme (`http` or `https`) and a host. If the URL is valid, it returns only the scheme and host. If not, it raises an error explaining that the callback needs a scheme and host.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` call this helper when they need to build bridge URLs on the same site as the callback. It keeps that small but important validation in one place, so the rest of the OAuth flow can assume it is redirecting through a proper web origin.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/token.py`

`domain_logic` · `connection consent and sandbox credential use`

This file solves a narrow but important problem: some command-line tools inside the sandbox need a real token, not just a remote service making API calls for them. GitHub is the key example. The GitHub CLI reads a token from an environment variable, and normal git-over-HTTPS expects a username and password-style credential. Without this file, a sandbox could have permission to use a GitHub account through Pipedream but still fail to clone, push, or create commits that GitHub correctly attributes to that account.

The file has two main jobs. First, it defines a small token reader, PipedreamGrantSecret, that asks Pipedream for an account token and keeps it briefly in memory. This is like keeping a freshly printed ticket in your pocket for a few minutes instead of walking back to the ticket desk every time you enter a room. The short cache avoids repeated broker calls during one burst of work.

Second, it builds the credential description attached to a connector. For GitHub, it also records how git should ask the GitHub CLI for credentials.

The commit_identity function handles the author identity for commits. It asks GitHub who the token belongs to, then builds GitHub’s private noreply email form, which still lets GitHub attribute the commit to the right account. It deliberately avoids guessing if GitHub’s answer is missing or invalid, because a wrong commit identity could make public history misleading.

#### Function details

##### `PipedreamGrantSecret.secret`  (lines 45–52)

```
async def secret(self, workspace_id: UUID, account_id: str) -> str
```

**Purpose**: This method returns the actual provider token for a connected Pipedream account. It keeps a token for a short time so repeated credential lookups in the same burst do not keep asking Pipedream for the same secret.

**Data flow**: It receives a workspace ID and a Pipedream account ID. It first checks its in-memory cache for that account and compares the saved expiry time with the current monotonic clock, which is a clock used for measuring elapsed time safely. If the cached token is still fresh, it returns it. Otherwise, it asks the Pipedream client for a new account token, stores that token with a new expiry time, and returns the token.

**Call relations**: When sandbox credential code needs the secret behind a CLI credential, this method is the piece that actually fetches it. It calls the shared Pipedream client at the moment of the read, rather than storing a client permanently, so test setups and transport overrides can still take effect.

*Call graph*: 2 external calls (monotonic, pipedream_client).


##### `cli_credential`  (lines 55–64)

```
def cli_credential(spec: pipedream.ConnectorSpec) -> CliCredential | None
```

**Purpose**: This function creates the credential description that tells the sandbox how to expose a connector’s token to command-line tools. If the connector does not declare a command-line environment variable, it returns nothing because that connector’s token should stay with Pipedream.

**Data flow**: It receives a connector specification from the Pipedream extension. If the specification has no CLI environment variable, the output is None. If it does, the function builds a CliCredential containing the environment variable name, the HTTP header name used for authorization, a PipedreamGrantSecret for fetching the token later, and GitHub git-credential settings for HTTPS git access.

**Call relations**: Higher-level connector setup can call this when reading a connector specification and deciding what, if anything, should be attached to the sandbox manifest. It constructs a PipedreamGrantSecret so the actual token is not fetched immediately; the token is only read later when the credential is used.

*Call graph*: 2 external calls (__init__, __init__).


##### `commit_identity`  (lines 67–124)

```
async def commit_identity(spec: pipedream.ConnectorSpec, account_id: str, workspace_id: UUID) -> CommitIdentity | None
```

**Purpose**: This function finds the Git author identity for a connected GitHub account. It is used so commits made between clone and push are attributed by GitHub to the same account whose token is doing the push.

**Data flow**: It receives a connector specification, an account ID, and a workspace ID. If the connector is not GitHub, it returns None because only the GitHub token is used this way. For GitHub, it asks Pipedream for the account token, then makes an authenticated request to GitHub’s /user endpoint. If GitHub refuses the request or returns an unusable record, it raises a PipedreamError instead of guessing. From a valid response, it reads the numeric GitHub user ID and login, chooses a safe display name, and returns a CommitIdentity with that name and GitHub’s noreply email address format.

**Call relations**: This is called during the flow that completes or records account consent, when the system wants to store the commit identity once instead of asking GitHub every time a sandbox opens. It relies on the Pipedream client for the token, uses an HTTP client to ask GitHub who the token belongs to, and hands back a CommitIdentity object for later git commits.

*Call graph*: 4 external calls (__init__, AsyncClient, PipedreamError, pipedream_client).


### `core/src/ufo/runtime/access/grants.py`

`domain_logic` · `request handling, OAuth callback, and connection administration`

This file is the project’s “permission desk” for external accounts. A member may connect an account through OAuth, which is the common web flow where you leave an app, approve access on another site, and return. The important rule is that the secret token stays server-side; agents and sandboxes only receive safe markers that the system can later swap for real access when needed.

The main story has two halves. `ConnectFlow.authorize` creates a provider login link and seals important details into a protected `state` value: workspace, member, agent, provider, conversation, and sharing choice. Later, `ConnectFlow.complete` opens that sealed state, exchanges the provider’s return code for an account, records the connection, grants the target agent, notifies extension hooks, and optionally resumes the conversation that asked for the connection.

`GrantStore` is the database-facing part. It creates or reuses connection rows, adds or removes grant edges, checks ownership and admin rights, toggles sharing, and disconnects accounts cleanly. The file also provides summary queries for user interfaces and audit views.

Without this file, agents would not have a safe, consistent way to gain access to member-owned external accounts. The system could lose track of who owns a token, accidentally widen private access, or fail to tell a waiting conversation that the connection is ready.

#### Function details

##### `grant_sentinel`  (lines 49–53)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds the placeholder value that represents a connected account when credentials are passed into a command-line environment. It is deterministic, so separate parts of the system can recognize the same account without sharing a secret.

**Data flow**: It takes an external account id as text, adds a fixed prefix, and returns the resulting marker string. It does not read or change stored data.

**Call relations**: This helper supports the wider grant system by giving sandbox setup and outbound access checks a shared, non-secret name for an account. The real token remains elsewhere.


##### `usable_cli_accounts`  (lines 56–76)

```
def usable_cli_accounts(grants: 'tuple[Grant, ...]', provider: str, member_id: UUID | None) -> tuple[str, ...]
```

**Purpose**: Chooses which connected accounts a command-line tool may use for one provider. It prefers the current member’s private grants, and falls back to shared grants if there are no private ones.

**Data flow**: It receives a set of grant records, a provider name, and an optional member id. It filters and sorts matching account ids, first looking for private grants owned by that member, then for shared grants, and returns the winning list.

**Call relations**: This function is used when turning grants into command-line access. It helps avoid silently picking one account when several are possible by returning the full set of usable account ids.


##### `UnknownProvider.__init__`  (lines 84–85)

```
def __init__(self, provider: str) -> None
```

**Purpose**: Creates a clear error message when someone asks to connect through a provider that is not installed or claimed by a resolver. The message is written for a member to read, not just for logs.

**Data flow**: It receives the unknown provider name, formats it into a sentence, and initializes the exception with that sentence. No other state changes.

**Call relations**: Provider lookup code in `ConnectFlow._provider` and provider validation in `ConnectFlow.validate_provider` use this when no matching connector can be found.

*Call graph*: called by 2 (_provider, validate_provider).


##### `OAuthProvider.provider`  (lines 146–146)

```
def provider(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose its provider slug, which is the stable internal name for that connector. Implementations supply the actual value.

**Data flow**: A concrete provider object returns its provider name when this property is read. The protocol itself stores nothing.

**Call relations**: The connect flow relies on this value after a provider exchange so the recorded connection is tied to the provider the connector actually represents.


##### `OAuthProvider.host`  (lines 149–149)

```
def host(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose the provider host that outbound access should be allowed to use. This helps the proxy know where a grant applies.

**Data flow**: A concrete provider object returns a host string when this property is read. The protocol only states the requirement.

**Call relations**: When `ConnectFlow.complete` records a grant, it passes this host into `GrantStore.record` so later outbound requests can be matched to the connected account.


##### `OAuthProvider.authorize_url`  (lines 151–151)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a provider builds the browser URL where a member approves access. Each connector supplies its own URL-building rules.

**Data flow**: It takes a sealed state value and a callback URL, and returns a provider-specific authorization link. The link carries enough information for the callback to verify the request later.

**Call relations**: `ConnectFlow.authorize` calls this after creating protected state, so the member can be sent to the provider’s consent screen.


##### `OAuthProvider.exchange`  (lines 153–155)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a provider turns the returned OAuth code into a connected account record. This is where the provider confirms what account was connected.

**Data flow**: It receives the provider’s code, the callback URL, the workspace id, and the original state. A concrete implementation talks to the provider and returns an `OAuthAccount` with account identity and optional commit identity.

**Call relations**: `ConnectFlow.complete` calls this during the callback, before anything is written to the grant store. The returned account is what becomes the durable connection.


##### `OAuthProviderResolver.claims`  (lines 166–166)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how an open provider resolver says whether it can serve a provider slug. This supports connector namespaces where providers are discovered dynamically rather than pre-registered one by one.

**Data flow**: It receives a provider name and returns true or false, possibly after checking an external catalog. It does not itself record a connection.

**Call relations**: `ConnectFlow.validate_provider` uses this to reject typos or unavailable providers before a consent link is minted.


##### `OAuthProviderResolver.descriptor`  (lines 168–168)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Defines how a resolver creates an OAuth provider descriptor for a provider name it can serve. This gives the connect flow the same interface as a normal installed provider.

**Data flow**: It receives a provider slug and returns an object that follows the `OAuthProvider` shape. The protocol leaves the construction details to the resolver implementation.

**Call relations**: `ConnectFlow._provider` uses this when a provider is not in the fixed provider map but a resolver is installed.


##### `ConnectionHooks.fire`  (lines 279–279)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Defines the notification point for extensions that need to react after a connection is recorded. For example, an extension might create feed-source rows based on the newly connected account.

**Data flow**: It receives a `ConnectionRecorded` payload describing the new or reused connection. Implementations perform their own side effects and return no meaningful value.

**Call relations**: `ConnectFlow.complete` calls this after the database transaction has recorded the grant, so extension work sees a real connection.


##### `ConnectResumption.resume`  (lines 292–299)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Defines how the system tells the original conversation that a requested connection has landed. This lets the agent continue without the member having to type another message.

**Data flow**: It receives the conversation id, a human-readable message, the speaking member id, and an idempotency key that prevents duplicate messages. It returns whether the resume message was successfully admitted.

**Call relations**: `ConnectFlow.complete` calls this near the end of the callback flow, after the connection and hook-derived state are in place.


##### `_resume_key`  (lines 302–317)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a stable idempotency key for the conversation resume message tied to one connect attempt. An idempotency key is a duplicate-prevention label: if the same callback is retried, the message is not sent twice.

**Data flow**: It receives the sealed OAuth state string, hashes it, keeps a short fixed-length part of the hash, adds a prefix, and returns the key. It never exposes the sealed state itself.

**Call relations**: `ConnectFlow.complete` uses this key when asking `ConnectResumption.resume` to tell the conversation that the account is connected.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 344–345)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently active in the runtime context. This avoids passing the workspace id into every store method.

**Data flow**: It reads the current workspace context and returns its id. It does not write anything.

**Call relations**: Most `GrantStore` database operations use this property to keep reads and writes scoped to the current workspace.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 348–349)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent id currently targeted by object dispatch. This is the agent whose grant edges the store should read or change.

**Data flow**: It reads the current object-scope agent id and returns it. It has no direct side effects.

**Call relations**: `GrantStore.record`, `active_grants`, `attach`, and grant permission checks use this value so grants apply to the intended agent.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 351–553)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None, commit: CommitIdentity | None=None, l
```

**Purpose**: Records a completed account connection and grants the current agent access to it. It also protects ownership, updates display and commit identity details, marks the asking turn as answered, and wakes parked feed sources that may now work again.

**Data flow**: It receives provider/account details, the member who granted access, conversation information, sharing choice, optional account label, optional commit identity, and optional turn id. It checks the account id, confirms the member still has a seat, inserts or reuses a connection row, refuses accounts owned by another member, upserts the grant edge, updates the turn if needed, clears retry/park state on related sources, and returns the connection id.

**Call relations**: `ConnectFlow.complete` calls this after the provider exchange succeeds. It is the durable landing point of the OAuth callback: after it returns, the connection exists and the agent can use it.

*Call graph*: 10 external calls (__init__, __init__, now, and_, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 555–609)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the current agent’s usable grants together with the owning member and account details. This is the read side used when an agent or sandbox needs to know what external accounts are available.

**Data flow**: It reads connector grant rows for the current workspace and agent, joins them to connection and member rows, converts each row into a `Grant` object, and includes commit identity when both name and email are present.

**Call relations**: Sandbox environment setup calls this when deciding what connected accounts can be exposed as safe credential markers.

*Call graph*: called by 1 (_grant_cli_env); 5 external calls (__init__, __init__, and_, select, workspace_tx).


##### `GrantStore.revoke`  (lines 611–623)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the acting member is allowed to do so. Revoking the edge stops that agent from using the connection but does not necessarily delete the underlying connection.

**Data flow**: It receives a grant id and actor member id. It checks that the grant belongs to the current agent and that the actor may mutate the connection, deletes the grant row if allowed, and returns whether anything was deleted.

**Call relations**: It delegates permission checking to `_grant_for_actor`, then performs the delete. User-facing grant removal flows would call this when a member withdraws an agent’s access.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 625–682)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Attaches an already existing connection to the current agent. It allows this only if the actor owns the connection or the connection is already shared across the workspace.

**Data flow**: It receives provider, account id, conversation id, actor member id, and a sharing claim. It locks and reads the matching connection, checks ownership or sharing, refuses attempts to newly share through attach, inserts the grant edge if missing, and returns true if the connection existed.

**Call relations**: This is used when no new OAuth exchange is needed because the account is already connected. It writes the same kind of grant edge that `record` writes after a fresh connect.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 684–712)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether a connection is shared with the workspace. It checks that the actor has authority before flipping the sharing flag.

**Data flow**: It receives a grant id, the desired shared value, and the actor member id. It verifies the grant and connection permissions, updates the connection row’s shared flag and timestamp, and returns whether a row changed.

**Call relations**: It uses `_grant_for_actor` to find and authorize the grant. Admins may be allowed for some narrowing actions, while widening access is more restricted.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 714–770)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully disconnects a connection the actor is allowed to remove. It also disables related feed sources and tombstones their pages so stale external content no longer appears live.

**Data flow**: It receives a connection id and actor member id. It checks permission, finds sources tied to that connection, removes source grants, marks sources removed, tombstones pages, deletes the connection row, and returns true if the operation was allowed and completed.

**Call relations**: It calls `_connection_for_actor` for the ownership/admin check. Because grant edges cascade from the connection, this is the broader cleanup path compared with `revoke`.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 772–800)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a particular connection. Owners are allowed; admins may be allowed depending on the operation.

**Data flow**: It receives a database connection, connection id, actor member id, and an admin-allowed flag. It locks and reads the connection, returns its id if the actor owns it, otherwise checks admin status and either returns the id or raises a permission error.

**Call relations**: `disconnect` calls this directly, and `_grant_for_actor` calls it while authorizing grant-level changes. `_is_admin` supplies the admin check.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 802–812)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Checks whether a member is an admin in the current workspace. It is a small permission helper.

**Data flow**: It receives a database connection and member id, reads the member row’s admin flag for the current workspace, and returns true or false.

**Call relations**: `_connection_for_actor` calls this when the actor is not the connection owner and the operation may allow admin authority.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 814–852)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Finds a grant for the current agent and confirms the actor may change the underlying connection. This prevents someone from editing another agent’s or another member’s grant by id alone.

**Data flow**: It receives a database connection, grant id, actor member id, and an admin-allowed flag. It looks up the grant’s connection, checks connection permission through `_connection_for_actor`, then locks and returns the grant id if it still matches.

**Call relations**: `revoke` and `set_shared` use this before changing grant or connection state. It is the bridge between grant-level operations and connection ownership rules.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 877–899)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, turn_id: UUID | None=None) -> str
```

**Purpose**: Creates the provider authorization URL that a member opens to start OAuth approval. It seals all details needed later so the callback does not need a separate pending-request row.

**Data flow**: It receives workspace, agent, provider, member, conversation, sharing, and optional turn ids. It resolves the provider, builds a `ConnectState`, encrypts that state, asks the provider to build an authorization URL, and returns the URL.

**Call relations**: `ConnectHandoff.authorize` calls this when a terminal connect request needs a fresh live consent link.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 901–906)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks that a provider name can be connected before the system offers the member a connect action. This catches unavailable connectors early.

**Data flow**: It receives a provider name. It accepts the name if it is directly installed or if a resolver claims it; otherwise it raises `UnknownProvider`.

**Call relations**: This is the stronger validation path for connect requests. It may call a resolver’s external catalog check, unlike the cheaper `knows_provider` check.

*Call graph*: calls 1 internal fn (__init__).


##### `ConnectFlow.knows_provider`  (lines 908–913)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether the connect flow could serve a provider name. It avoids external catalog validation and is meant for repeated checks while holding database locks.

**Data flow**: It receives a provider name and returns true if the provider is directly installed or if any resolver is installed. It does not perform network input/output.

**Call relations**: `ConnectHandoff.authorize` uses this when a member presses an existing connect control and the system needs a fast sanity check.


##### `ConnectFlow.bridge_workspace`  (lines 915–921)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and identifies which workspace it belongs to. A browser bridge is an intermediate request that must match the sealed state before it can run as a workspace.

**Data flow**: It receives a sealed state, provider name, and callback URL. It opens the state, checks that the provider and callback match, confirms the provider is known, and returns the workspace id from the state.

**Call relations**: `connect_bridge_workspace` calls this through the installed connect flow when handling bridge requests from a web request.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 923–974)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Completes the OAuth callback: verifies the state, exchanges the provider code, records the connection and grant, notifies extensions, and optionally resumes the waiting conversation.

**Data flow**: It receives a sealed state and provider code. It opens the state, resolves the provider, enters the correct workspace and agent context, exchanges the code for account details, records the grant through `GrantStore.record`, fires connection hooks if present, sends a resume message if present, and returns a `GrantRecorded` summary.

**Call relations**: This is the main callback path after the member approves access. It calls `_open`, `_provider`, the provider’s `exchange`, the store’s `record`, optional hook/resumption interfaces, and `_resume_key` for duplicate-safe conversation messages.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 976–981)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns the member-facing display name for a provider. If no explicit label is configured, it turns the provider slug into readable title-case words.

**Data flow**: It receives a provider slug, looks it up in the labels map, and otherwise replaces underscores with spaces and title-cases the result.

**Call relations**: `ConnectFlow.complete` uses this label in the callback result and in the message sent to the resumed conversation.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 983–989)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. It supports both directly installed providers and providers supplied by an open resolver.

**Data flow**: It receives a provider name, checks the fixed provider map, then asks the resolver for a descriptor if one exists, and raises `UnknownProvider` if neither path works.

**Call relations**: `authorize`, `bridge_workspace`, and `complete` call this whenever they need provider-specific OAuth behavior.

*Call graph*: calls 1 internal fn (__init__); called by 3 (authorize, bridge_workspace, complete).


##### `ConnectFlow._open`  (lines 991–996)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state carried through the browser redirect. It rejects tampered or expired state.

**Data flow**: It receives the state string, asks Fernet encryption to decrypt it with a time limit, parses the JSON into `ConnectState`, and returns that object. If decryption fails, it raises `ConnectStateInvalid`.

**Call relations**: `bridge_workspace` and `complete` call this before trusting any workspace, member, provider, or agent information from the browser.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 1024–1109)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Hands a member a safe OAuth URL for a connect request attached to a conversation turn. It reuses a recent URL when possible and mints a new one when the old one is too close to expiry.

**Data flow**: It receives workspace id, turn id, and member id. It locks and reads the turn, validates that the terminal connect request still exists and belongs to the member, checks the provider and target agent, returns a still-fresh memoized URL if present, otherwise asks `ConnectFlow.authorize` for a new URL, stores it on the turn, and returns it.

**Call relations**: This is the surface-facing entry for pressing a connect control. It uses `_held` to decide whether a stored URL is still safe and delegates new URL creation to the connect flow.

*Call graph*: calls 1 internal fn (_held); 5 external calls (__init__, model_validate, select, update, workspace_tx).


##### `ConnectHandoff._held`  (lines 1111–1122)

```
def _held(self, url: str | None, authorized_at: datetime | None) -> str | None
```

**Purpose**: Decides whether a memoized authorization URL is still fresh enough to use. This avoids sending a member to a provider page with a state value that may expire before they return.

**Data flow**: It receives a URL and the time it was authorized. If either is missing or the timestamp is older than the memo window, it returns none; otherwise it returns the URL.

**Call relations**: `ConnectHandoff.authorize` calls this before minting a new URL and again after a race, to return a URL another caller may just have written.

*Call graph*: called by 1 (authorize); 3 external calls (now, replace, timedelta).


##### `install_connect_flow`  (lines 1128–1136)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow singleton. This lets tools, web callbacks, and surfaces find the same configured OAuth machinery without passing it through every call.

**Data flow**: It receives a `ConnectFlow` or none and stores it in a module-level variable. Passing none disables the flow.

**Call relations**: Startup code or tests call this to set the active flow. `installed_connect_flow` later reads the stored value.


##### `installed_connect_flow`  (lines 1139–1142)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed connect flow or fails loudly if connection support is unavailable. This prevents silent behavior when credentials were not configured.

**Data flow**: It reads the module-level installed flow. If present, it returns it; if absent, it raises `ConnectUnavailable`.

**Call relations**: `connect_bridge_workspace` calls this before verifying bridge requests. Other runtime paths can use it as the central access point for connect support.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 1145–1154)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Extracts and verifies the workspace for a connector browser bridge request. If anything is invalid or connect support is unavailable, it rejects the request by returning none.

**Data flow**: It receives a Starlette web request, reads `state`, `provider`, and `callback` query parameters, asks the installed connect flow to verify them, and returns the workspace id or none on known validation failures.

**Call relations**: This is a small web-facing wrapper around `ConnectFlow.bridge_workspace`, designed so request handling can decide whether to admit a bridge request into a workspace.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 1161–1170)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, human-readable object name for a provider account. It includes a short hash so two accounts with similar cleaned-up names do not collide.

**Data flow**: It receives provider and account id strings, slugifies both, hashes the combined raw identity, truncates the readable part to fit the object-name limit, and returns the final name.

**Call relations**: Surfaces that name connection and connector-grant objects can use this so the same account is displayed consistently everywhere. It uses `_slug` for the readable pieces.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 1173–1174)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a simple lowercase slug suitable for object names. A slug is a cleaned-up identifier made of letters, numbers, and dashes.

**Data flow**: It receives raw text, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and returns the result.

**Call relations**: `account_object_name` calls this for both provider and account id before adding the hash qualifier.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 1177–1185)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of connector grants for the current object-dispatch agent. This is useful for showing what accounts this agent can use.

**Data flow**: It builds a database scope for the current workspace and current object agent, then delegates the actual query and row conversion to `_grant_summaries`.

**Call relations**: This is the agent-scoped wrapper around the shared summary query helper.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 1188–1191)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of all connector grants in a workspace. This is meant for operator or workspace-wide views.

**Data flow**: It receives a workspace id, enters that workspace context, and asks `_grant_summaries` to query all grants for that workspace.

**Call relations**: This is the workspace-wide wrapper around `_grant_summaries`, unlike `grant_summaries`, which is limited to one agent.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 1194–1245)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared query that turns connector grant rows into readable grant summaries. It joins grants to connections, agents, and member emails.

**Data flow**: It receives a database filter expression describing the desired scope. It queries matching grants, orders them by provider and agent name, converts rows into `GrantSummary` objects, and returns them.

**Call relations**: Both `grant_summaries` and `workspace_grant_summaries` call this so the row-building logic stays in one place.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 4 external calls (__init__, and_, select, workspace_tx).


##### `connection_summaries`  (lines 1248–1325)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists all member-owned connections in the current workspace, including which agents currently have grants to each one. This supports connection management screens.

**Data flow**: It reads connection rows, owner member emails, and any joined grant/agent names for the current workspace. It groups repeated rows by provider and account id, collects agent names, and returns `ConnectionSummary` objects.

**Call relations**: This is a read-side companion to the write operations in `GrantStore`. It gives a workspace-level view independent of the currently bound agent.

*Call graph*: 5 external calls (__init__, and_, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1328–1363)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. These are the accounts that feed registration can use by default.

**Data flow**: It queries current-workspace connections joined through grant rows to agents marked as main, orders by provider and account id, and returns `MainAgentConnection` objects.

**Call relations**: Feed registration code can use this to find member-owned accounts already available to the main agent, while ignoring accounts connected only for other shipped agents.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/runtime/surfaces/cli.py`

`io_transport` · `request handling`

This file is the browser-facing return point for connecting an external account, such as through OAuth, which is a common sign-in-and-permission handoff used by services like Google or Slack. When a provider sends the browser back with a temporary code and a sealed state value, this file verifies that handoff, records the newly connected account, and shows the user a simple completion page.

The important idea is that this page cannot rely on a normal logged-in web session. The browser only brings back the state value created earlier, so the server must use that state to decide whether the callback is real and where the user should go next. If this deployment has a public browser portal, the page points the user toward the connectors screen and can forward them there with the newly connected account named in the URL. If there is no browser portal, the page simply says the account connected and tells the user whether to close the tab or continue the conversation elsewhere.

The file also serves a fixed SVG logo. That keeps the callback page self-contained: it can draw the right branding even when no frontend build or session-specific assets are available.

#### Function details

##### `portal_url`  (lines 52–62)

```
def portal_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: Builds the public browser address for the deployment’s home surface, if one exists. It is used when the system needs to know whether there is a connectors screen the user can be sent to after connecting an account.

**Data flow**: It receives a public base URL and the name of the browser surface. If either value is missing, it returns nothing, meaning there is no browser portal to send the user to. If both are present, it trims any trailing slash from the base URL and appends `/surface/<home surface>`, producing the portal address.

**Call relations**: This helper is meant to be used when setting up or carrying the connect flow, so the later callback can already know where the browser portal lives. The callback page then uses that stored portal address to decide whether to forward the user to the connectors screen or just show a close-this-tab message.


##### `connect_callback`  (lines 69–105)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: Finishes the connector authorization trip after the outside provider redirects the browser back. It checks the returned state and code, records the connected account, and returns a friendly completion page.

**Data flow**: It receives `state` and `code` from the callback URL. First it asks for the installed connect flow; if connecting is unavailable, it returns a service-unavailable error. If the state or code is missing, it returns a bad-request error. It then gives the state and code to the connect flow, which verifies the state and exchanges the code for the connected account record. If that succeeds, it builds a readable account name and returns an HTML callback page. When a portal URL exists, that page links or forwards to the connectors screen with the connected account name included. When no portal URL exists, the page says the connection is complete and either tells the user the conversation continues or that they can close the page.

**Call relations**: FastAPI calls this function when a browser visits `/v1/connect/callback`. Inside the request, it calls `installed_connect_flow` to get the active connector flow, then asks that flow to complete the OAuth handoff. For successful callbacks, it uses `urlencode` to safely place the connected account name in a URL, creates a `PageLink` for the connectors screen, and hands everything to `callback_page` to render the final HTML. When something is wrong, it raises `HTTPException` so FastAPI sends the proper error response.

*Call graph*: 5 external calls (__init__, HTTPException, installed_connect_flow, callback_page, urlencode).


##### `connect_logo`  (lines 109–117)

```
async def connect_logo() -> Response
```

**Purpose**: Serves the UFO logo used by the connector callback page. This matters because the callback page may be reached without access to the normal frontend assets.

**Data flow**: It reads the SVG logo file from disk, wraps those bytes in an HTTP response, marks the media type as `image/svg+xml`, and adds a long cache header so browsers can safely keep the logo for a long time.

**Call relations**: FastAPI calls this function when a browser requests `/v1/connect/logo.svg`. The function does not involve the connector flow itself; it simply hands back a `Response` containing the logo so the callback page can display consistent branding.

*Call graph*: 1 external calls (Response).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector setup, request handling, and action execution`

This file is the bridge between UFO and Pipedream’s hosted connector system. Pipedream does the sensitive work of asking a user for permission, storing refreshable app credentials, and running provider actions on the server side. UFO keeps only a connected-account id, like a claim ticket, and asks Pipedream to use it when needed.

The file starts by listing the connector types this Pipedream extension supports. Each connector entry says the human name, the Pipedream app slug, and sometimes special OAuth settings. For example, GitHub can return a real access token for command-line tools, while many other providers are used only through Pipedream actions.

The main class, `PipedreamClient`, wraps Pipedream’s REST API. REST API means ordinary web requests over HTTP. It can mint a browser consent link, look up connected accounts, list and run Pipedream actions, and fetch a provider token when that is allowed. Before using an account, it checks ownership. This is important because the project-level Pipedream token can technically read many accounts; without these checks, a bug could accidentally let one workspace act as another.

A small access-token cache avoids asking Pipedream for a new client credential token on every request. Helper functions turn loose JSON replies into safer Python objects and raise clear errors when Pipedream returns bad data.

#### Function details

##### `PipedreamError.__init__`  (lines 120–123)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception for a failed or unusable Pipedream response. It keeps both the HTTP status code and the response body so callers can report what went wrong instead of silently pretending no connector exists.

**Data flow**: It receives a numeric status and a text body from some failed operation. It turns them into a message like `pipedream 403: ...`, stores the original pieces on the error object, and raises or passes that object back through normal exception flow.

**Call relations**: Many parts of this file and the broker code use this error whenever Pipedream refuses a request, returns missing fields, or an ownership check fails. It is the common way failures leave the Pipedream client and reach higher-level connector code.

*Call graph*: called by 13 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, account_token, connect_token, newest_account, workspace_account (+3 more)).


##### `PipedreamClient.access_token`  (lines 161–182)

```
async def access_token(self) -> str
```

**Purpose**: Gets the short-lived bearer token that lets this deployment call Pipedream’s API. A bearer token is like a temporary pass: whoever presents it can make approved API calls.

**Data flow**: It first checks the process-wide cache for a still-valid token for this client id. If the cached token is missing or near expiry, it posts the client id and secret to Pipedream’s OAuth token endpoint, reads the returned token and lifetime, stores them in the cache, and returns the token string.

**Call relations**: `_get` and `_post` call this before making authenticated Pipedream API requests. It uses `_http` to create the temporary HTTP client and `_body` to validate the token response; if the response lacks a usable access token, it raises `PipedreamError`.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 184–199)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a Pipedream Connect token and hosted consent link for a user. This is the link the user opens in a browser to approve access to an outside app.

**Data flow**: It receives the external user id plus success and error redirect URLs. It posts those to Pipedream, checks that the response contains both a token and a connect-link URL, and returns them together as a `ConnectToken` object.

**Call relations**: Higher-level OAuth or broker flow calls this when it needs to start a new app connection. It hands the real API call to `_post`, and it raises `PipedreamError` if Pipedream does not return the pieces needed to send the user through consent.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 201–208)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Looks up a connected account and proves it belongs to the expected external user. This prevents the system from accidentally using an account connected by someone else.

**Data flow**: It receives a Pipedream account id and an expected external user id. It fetches the account record, unwraps the `data` field if Pipedream used one, then passes the record through `_owned_account`, which checks health and ownership. It returns a clean `ConnectedAccount` object.

**Call relations**: This is used after a consent flow or when code needs to bind a grant to a specific state-scoped user. It relies on `_get` for the web request, `_dict` for safe JSON shape handling, and `_owned_account` for the security check.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 210–214)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Fetches the display name Pipedream has for a connected account, if one is available. This is useful for showing a human-friendly label in user interfaces.

**Data flow**: It receives an account id, fetches that account from Pipedream, unwraps the account record, and reads its `name` field. If the name is a non-empty string, it returns it; otherwise it returns `None`.

**Call relations**: This is a lightweight lookup alongside the stricter account-reading methods. It calls `_get` to talk to Pipedream and `_dict` to avoid crashing if the response shape is not exactly as expected.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 216–226)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Looks up a connected account and confirms that it belongs to a particular workspace. A workspace is a tenant-like boundary; this check stops cross-workspace account use.

**Data flow**: It receives an account id and a workspace UUID. It fetches the account record, turns it into a `ConnectedAccount`, then checks whether the account’s external user id matches the workspace’s allowed user-id pattern. It returns the account on success or raises `PipedreamError` on mismatch.

**Call relations**: Broker execution code can use this before accepting an account id for a workspace. It combines `_get`, `_dict`, `_account`, and `_workspace_owns_external_user`; the last helper is the key ownership gate.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.account_token`  (lines 228–252)

```
async def account_token(self, account_id: str, workspace_id: UUID) -> str
```

**Purpose**: Fetches the real provider access token behind a connected account, but only after confirming the account belongs to the workspace. This is used for special cases like GitHub command-line authentication where a proxied action is not enough.

**Data flow**: It receives an account id and workspace UUID. It fetches the account with credentials included, validates the account record and workspace ownership, then reads `oauth_access_token` from the credentials section. It returns the token string or raises an error if no token is available.

**Call relations**: This is one of the most sensitive paths in the file because it can return an actual secret. It uses the same `_account` and `_workspace_owns_external_user` checks as normal account reads before touching credentials, and raises `PipedreamError` if Pipedream withholds the token.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 254–268)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for a given external user and app. This helps match a just-finished browser consent flow back to the account it created.

**Data flow**: It receives an external user id and Pipedream app slug. It asks Pipedream for matching accounts, keeps only dictionary-shaped records, chooses the one with the latest `created_at` value, checks that it has an id, and verifies ownership before returning a `ConnectedAccount`.

**Call relations**: OAuth callback or exchange code would use this after the user returns from Pipedream’s hosted consent page. It calls `_get` for the listing, `_dict` for response cleanup, and `_owned_account` to make sure the newest account really belongs to that external user.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 270–299)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists Pipedream actions available for a provider app, optionally filtered by a search query. Actions are ready-made server-side operations, such as sending a Discord message or searching Linear issues.

**Data flow**: It receives an app slug and optional query text. It repeatedly asks Pipedream for pages of action rows, follows the cursor for the next page, and stops when the page is short, the cursor is missing, or the maximum listing size is reached. It returns a tuple of action dictionaries.

**Call relations**: The Pipedream broker calls this when it needs to discover actions, especially after a tool key is missing. It uses `_get` for each page and `_dict` to read pagination details safely.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 301–302)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition of one Pipedream component or action by key. This tells higher-level code what inputs the action expects and how it is described.

**Data flow**: It receives a component key string. It sends a GET request to Pipedream’s component endpoint and returns the response dictionary exactly as validated by `_get` and `_body`.

**Call relations**: Broker or tool-description code can call this when it needs more than the catalog row from `list_actions`. It delegates all authentication and response checking to `_get`.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 304–322)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on the server side for a specific external user. This lets UFO use the connected account without directly storing or sending the provider’s secret.

**Data flow**: It receives an action key, an external user id, and configured action properties. It builds the Pipedream run request, adds a fresh file stash so files created by the action can be retrieved later, checks that the request body is not too large, posts it, and returns Pipedream’s response dictionary.

**Call relations**: The broker calls this when a dynamic Pipedream-backed tool is executed. It uses `_post` for the authenticated request, and it rejects oversized arguments before sending them to Pipedream.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 324–327)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a validated JSON object. GET requests are used for reading data, such as accounts, actions, and component definitions.

**Data flow**: It receives an API path and optional query parameters. It gets a bearer token from `access_token`, creates an HTTP client with `_http`, sends the GET request, and passes the response through `_body`. The result is a dictionary or an exception.

**Call relations**: All read-style public methods in this class go through `_get`. It centralizes the repeated steps of authentication, HTTP client creation, and response validation.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 7 (account_label, account_token, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 329–332)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a validated JSON object. POST requests are used when creating something or asking Pipedream to run an action.

**Data flow**: It receives an API path and a dictionary body. It gets a bearer token, opens an HTTP client with that token, posts the body as JSON, and sends the response through `_body`. It returns the parsed response dictionary or raises an error.

**Call relations**: `connect_token` and `run_action` use this for their write-style API calls. Like `_get`, it keeps authentication and response checking in one place.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 334–347)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates a short-lived asynchronous HTTP client configured for Pipedream. Asynchronous means it can wait for network replies without blocking the whole program.

**Data flow**: It receives an optional bearer token. If a token is present, it adds authorization and environment headers; if not, it creates an unauthenticated client for the OAuth token request. It returns an `httpx.AsyncClient` with the Pipedream base URL, timeout, headers, and optional test transport.

**Call relations**: `access_token`, `_get`, and `_post` all use this to open network clients. It is the one place that decides which headers every Pipedream request carries.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 350–351)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This prevents confusing JSON shapes from causing accidental attribute errors elsewhere.

**Data flow**: It receives any Python object. If the object is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Many account and listing functions use this when reading Pipedream JSON fields that might be absent or malformed. It is a small guardrail used before deeper validation happens.

*Call graph*: called by 7 (account_label, account_token, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 354–367)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that a Pipedream account record is healthy and belongs to the exact external user expected. This is a core safety check against using someone else’s connected account.

**Data flow**: It receives a raw account record, the account id being read, and the expected external user id. It first converts the record with `_account`, then compares the account’s recorded owner to the expected owner. It returns the account if they match or raises `PipedreamError` if they do not.

**Call relations**: `connected_account` and `newest_account` use this after fetching account data from Pipedream. It builds on `_account`, which checks basic record validity and grant health.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 370–393)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into a clean `ConnectedAccount`, while refusing records that cannot safely authenticate. It treats an unhealthy grant as something the user must reconnect, not as a retryable server glitch.

**Data flow**: It receives a raw account dictionary and the account id. It reads the external owner, rejects the record if that owner is missing, raises `GrantUnusable` if Pipedream says the account is unhealthy, reads the app slug if present, and returns a `ConnectedAccount` with the account id, app, and owner.

**Call relations**: This is the shared account-shaping helper for workspace checks, token reads, and exact-owner checks. It calls `_dict` to safely inspect the nested app field and raises either `PipedreamError` for bad data or `GrantUnusable` for a user-fixable broken grant.

*Call graph*: calls 3 internal fn (__init__, __init__, _dict); called by 3 (account_token, workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 396–397)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used for Pipedream external user ids that belong to a workspace. This gives all connection-specific user ids a recognizable namespace.

**Data flow**: It receives a workspace UUID. It formats the UUID’s compact hexadecimal form into a string starting with `ufo_` and ending with an underscore, then returns that prefix.

**Call relations**: `connection_user_id` uses this when creating a new state-scoped external user id, and `_workspace_owns_external_user` uses it when checking whether an existing external user id belongs to the workspace.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 400–409)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user id belongs to a given workspace. This is the workspace-level ownership test used before accepting an account or releasing a token.

**Data flow**: It receives a workspace UUID and an external user id string. It accepts an older direct workspace form, or checks for the standard workspace prefix followed by a fixed-length lowercase hexadecimal connection id. It returns `True` for a valid match and `False` otherwise.

**Call relations**: `workspace_account` and `account_token` call this after reading the account owner from Pipedream. It uses `workspace_user_prefix` so creation and validation follow the same naming pattern.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 2 (account_token, workspace_account).


##### `connection_user_id`  (lines 412–414)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for a workspace connection attempt. It ties the id to both the workspace and the OAuth state value without exposing the raw state.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first fixed number of hexadecimal characters, appends that to the workspace prefix, and returns the final external user id.

**Call relations**: Connection setup code can use this before calling `connect_token`, so the account created by Pipedream is labeled with a state-scoped owner. `_workspace_owns_external_user` later recognizes the same format.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 417–425)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe dictionary or raises a clear error. It is the response gatekeeper for all Pipedream calls in this file.

**Data flow**: It receives an `httpx.Response`. If the status code is an error, it raises `PipedreamError` with the status and text. If the response has no content, it returns an empty dictionary. Otherwise it parses JSON and requires that the JSON be an object-shaped dictionary.

**Call relations**: `access_token`, `_get`, and `_post` all pass their HTTP responses through this helper. That means public methods can assume they got a dictionary and do not each need to repeat basic HTTP error handling.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 428–446)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a `PipedreamClient` from environment variables. This is the deployment’s default way to get a configured Pipedream API client.

**Data flow**: It reads the Pipedream client id, client secret, project id, and optional environment name from process environment variables. If the required values are missing, it raises a runtime error; otherwise it returns a new `PipedreamClient` with those settings.

**Call relations**: Higher-level broker or setup code calls this when it needs the real Pipedream client for the current deployment. It keeps configuration lookup in one place and fails early if the connector broker cannot be used.

*Call graph*: 1 external calls (__init__).


### Credential vaulting and direct authentication
Defines API-key connector manifests, securely stores workspace credentials, and supports direct or provider-specific credential login flows.

### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest load`

Most connected services in this system can be reached through a brokered consent flow, but many services simply give users an API key. This file is the bridge for those services. It lists providers such as Datadog, PostHog, Mercury, Apollo, and PandaDoc, then describes what key each one needs, which HTTP header the key belongs in, and which host the key may be sent to.

The important safety idea is that the sandbox does not receive the real secret. Instead, it sees an environment variable containing a sentinel, meaning a harmless marker value. When code in the sandbox calls the provider’s API, an egress proxy replaces that marker with the real key on the wire. This is like giving a courier a sealed envelope instead of letting them read the password inside.

Some providers have one fixed API host. Others, like Datadog, have several allowed regional hosts. For those, this file creates an extra credential slot where the workspace owner chooses from a fixed list. That prevents a key meant for one region from being sent to an arbitrary hostname.

At the end, the file builds a manifest: a compact description of all credential slots and user-facing instructions. Without this file, these API-key-based providers would not appear as fillable credentials, and the proxy would not know where or how to safely inject their keys.

#### Function details

##### `KeyedSecret.__post_init__`  (lines 56–61)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a declared secret uses only an authentication scheme the proxy knows how to safely replace. It prevents someone from adding a provider declaration that looks valid but cannot actually be swapped onto outgoing requests.

**Data flow**: A newly created KeyedSecret has a key name, header name, environment variable name, description, and optional scheme such as Bearer. This method reads the scheme after construction. If the scheme is blank or one of the allowed schemes, nothing changes; if it is unsupported, construction fails with a clear error.

**Call relations**: This runs automatically when a KeyedSecret row is created in the provider table. It acts as an early guard before any provider can contribute credential slots to KeyedProvider.slots or user instructions to KeyedProvider.usage.


##### `KeyedProvider.__post_init__`  (lines 79–88)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider declaration describes its API host in exactly one safe way. A provider must either have one fixed host or a fixed list of selectable hosts, but not both and not neither.

**Data flow**: A newly created KeyedProvider arrives with its provider name, label, secrets, and either a single host or a list of sites. The method checks those fields. If the host setup is valid, the provider is accepted unchanged; if the setup is ambiguous or incomplete, it raises an error before the bad declaration can be used.

**Call relations**: This runs automatically when each provider in KEYED_PROVIDERS is created. It protects later steps such as KeyedProvider.target_host and KeyedProvider.slots, which rely on the host information being clear and safe.


##### `KeyedProvider.target_host`  (lines 91–100)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This turns the provider’s host settings into the form used by credential injection. For a fixed-host provider it returns the hostname directly; for a multi-site provider it builds a HostChoice, which is a fixed menu of allowed hosts.

**Data flow**: It reads the provider’s host, site list, host environment variable, and site description. If there is no site list, it outputs the fixed host string. If there is a site list, it creates and returns a HostChoice containing the credential slot name, explanation, allowed hosts, default host, and environment variable used in the sandbox.

**Call relations**: KeyedProvider.slots calls this when creating injection rules, and KeyedProvider.usage calls it when writing example instructions. When the provider has selectable sites, this function hands the details to HostChoice.__init__ so the manifest can represent the user’s host choice safely.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 102–120)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This converts one provider declaration into the credential slots the system can ask a workspace owner to fill. Each slot says what secret is needed and exactly where it may be injected into an outgoing request.

**Data flow**: It starts with a provider and its secrets. For each secret, it creates a CredentialSlot with a name, a human description, and an InjectionTarget that includes the allowed host, HTTP header, sentinel value, sandbox environment variable, and request dimension. If the provider has a selectable host, it also adds a separate slot for that host choice. The result is a tuple of credential slot declarations.

**Call relations**: The top-level manifest function calls this for every provider in KEYED_PROVIDERS. Inside the function, InjectionTarget.__init__ records how the proxy should swap the sentinel for the real key, and CredentialSlot.__init__ packages that rule into something the credential system can show and fill.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 122–135)

```
def usage(self) -> str
```

**Purpose**: This writes a short, human-readable usage line for one provider. It tells the agent which credential slots exist and shows the shape of a curl command that would call the provider using the sandbox environment variables.

**Data flow**: It reads the provider’s secrets, headers, schemes, environment variable names, and host setup. It formats those pieces into a sentence with slot names and an example HTTPS request. The output is plain text that becomes part of the prompt section shown to the agent.

**Call relations**: The module uses this while building SECTION_BODY, once for each provider. It depends on KeyedProvider.target_host to know whether the example should use a fixed hostname or a selected host environment variable.


##### `manifest`  (lines 284–290)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public description for the rest of the system. It returns the manifest containing all keyed-provider credential slots and the instructions that explain how to use them safely.

**Data flow**: It reads the extension name, version, provider table, and prepared prompt text. It asks each provider for its slots, combines them into one credentials list, wraps the explanatory text in a PromptSection, and returns a Manifest object. Nothing is written to disk or sent over the network here; it produces structured configuration for the host system.

**Call relations**: The extension loader calls this when it needs to discover what the extension contributes. The function hands credential declarations to Manifest.__init__ and wraps the keyed-provider guidance with PromptSection.__init__, so later parts of the system can request missing credentials and tell the sandbox how to call these APIs.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/runtime/access/credentials.py`

`domain_logic` · `cross-cutting credential prompt, storage, and proxy injection`

This file solves a sensitive problem: users need to give the system private credentials, but those secrets must not appear in chat logs, sandbox output, or plain database rows. Think of it like a locked mailbox. The user drops a secret into a trusted slot, the system stores only a locked version, and later only the proxy can unlock it when it must swap the real secret into an outbound request.

The file has three main jobs. First, it names credential slots in a stable way, so extensions can declare places where secrets belong. Second, it creates and checks sealed credential requests. A sealed request is an encrypted note saying which workspace, member, and slot a private prompt is allowed to fill. If someone tampers with it, reuses it too late, or tries to apply it to another slot, it is rejected. Third, `CredentialStore` writes, reads, updates, clears, and rotates encrypted credential values in the database.

It also supports `HostChoice`, where a provider may have a small declared list of possible API hosts. The stored value is only a choice from that list, not free text. That matters because the proxy may trust this host when allowing network access; letting users type arbitrary hostnames there could become a security hole.

#### Function details

##### `deploy_env`  (lines 33–39)

```
def deploy_env(name: str) -> str | None
```

**Purpose**: Looks up a deployment-level secret from environment variables. It prefers a `UFO_`-prefixed name so this project can have its own copy of a key without accidentally sharing the generic variable with other tools.

**Data flow**: It receives a variable name, checks `UFO_<name>` first, then checks `<name>`, and treats an empty value as missing. It returns the found string or `null` if neither usable value exists.

**Call relations**: No caller is shown in the provided graph. It is a small helper for code that needs to read platform-provided secrets before credential storage or provider setup can work.


##### `credential_object_name`  (lines 42–45)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a clean object-style name. This gives the rest of the system a predictable label to use when showing, reading, or deleting a credential slot.

**Data flow**: It receives a slot name, lowercases it, replaces runs of non-letter-or-number characters with hyphens, and trims hyphens from the ends. It returns the cleaned name.

**Call relations**: `named_slots` calls this when it builds the public names for declared credential slots. It delegates the text replacement to the regular-expression library.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `member_slot`  (lines 51–55)

```
def member_slot(slot: str, member_id: UUID) -> str
```

**Purpose**: Builds the private storage key for one member's own version of a credential slot. This lets the same workspace store both shared credentials and member-specific credentials without mixing them up.

**Data flow**: It receives a base slot name and a member ID. It combines them with a fixed marker string, producing one unique slot name for that member.

**Call relations**: No caller is shown in the provided graph. It is a naming helper for code that needs to store or fetch credentials tied to a particular person.


##### `named_slots`  (lines 58–73)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Creates the public object names for a set of declared credential slots. If two slots would get the same cleaned name, it adds a short stable fingerprint so both can still be addressed safely.

**Data flow**: It receives declared slots, groups them by their cleaned credential object name, and returns a dictionary from final object name to the slot declaration. For name collisions, it uses the slot's extension and original name to create a short hash suffix.

**Call relations**: It calls `credential_object_name` for the human-readable base name and `hashlib.sha256` for collision-proof suffixes. Other parts of the system can then use the returned names to refer to the same credential instances consistently.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 102–103)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Encrypts a credential request state into a sealed string. The sealed string can be handed through less-trusted paths because its contents cannot be changed or read without the deployment's key.

**Data flow**: It receives a Fernet encryptor and a `CredentialRequestState`. It turns the state into JSON bytes, encrypts those bytes, and returns the encrypted token as text.

**Call relations**: `CredentialRequests.seal` uses this for normal private credential prompts, and `CredentialRequests.authorize` uses it for provider authorization handoffs. It relies on the state model's JSON output and Fernet encryption.

*Call graph*: called by 2 (authorize, seal); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 106–120)

```
def open_credential_request(fernet: Fernet, sealed: str, *, ttl: int=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Decrypts and validates a sealed credential request. It gives callers one clear failure type when the token is expired, forged, malformed, or not made by this deployment.

**Data flow**: It receives a Fernet encryptor, a sealed string, and a time-to-live limit. It decrypts the token, checks that it has not expired, parses the JSON into a credential request state, and returns that state. If anything is wrong, it raises `CredentialRequestInvalid`.

**Call relations**: `CredentialRequests.open_authorization` calls this before checking whether an authorization belongs to the expected workspace, member, and slot. It hands low-level Fernet failures and model validation failures back as one domain-specific error.

*Call graph*: called by 1 (open_authorization); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 132–145)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed prompt request for one member to fill one or more declared credential slots. It prevents prompts from naming credential slots that no installed extension declared.

**Data flow**: It receives a workspace ID, member ID, and slot names. It checks every slot against the known declared set, adds a new request ID and current issue time, seals the state, and returns the encrypted request token.

**Call relations**: It calls `seal_credential_request` after constructing a `CredentialRequestState`. The timestamp and random request ID come from the standard time and UUID helpers, so later fulfillment can identify and limit the request.

*Call graph*: calls 1 internal fn (seal_credential_request); 3 external calls (__init__, time, uuid4).


##### `CredentialRequests.authorize`  (lines 147–160)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization token for a provider-specific credential flow, such as an external service redirect that carries temporary state. It limits the token to one declared slot and refuses empty provider state.

**Data flow**: It receives the workspace, member, slot, and provider payload. It checks that the slot is declared and the payload is not empty, wraps those claims into request state, encrypts it, and returns the sealed string.

**Call relations**: It uses `seal_credential_request`, the same sealing helper as normal credential prompts. The resulting token is later opened by `CredentialRequests.open_authorization` when the provider flow returns.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 162–176)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Opens a sealed provider authorization and proves it belongs to the exact workspace, member, and slot currently being completed. This stops a valid token from being replayed in the wrong place.

**Data flow**: It receives a sealed token plus the expected workspace ID, member ID, and slot. It decrypts the token, compares each claim with the expected values, checks the slot is still declared, and returns the stored provider payload. It raises an error if any check fails.

**Call relations**: It calls `open_credential_request` for the basic decrypt-and-parse step, then adds authorization-specific checks. If the token is valid, it hands the provider payload back to the code finishing the authorization.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `CredentialStore.put`  (lines 183–205)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a credential value for a workspace slot, encrypted before it reaches the database. It is useful when code already has authority to set a slot directly.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty secret, encrypts the value, opens a workspace database transaction, and updates the existing row or inserts a new one. It does not return a value; the database is changed.

**Call relations**: It uses `workspace_tx` to write within a transaction and SQLAlchemy insert/update statements to change the credential table. No caller is shown in the provided graph.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.fulfill`  (lines 207–294)

```
async def fulfill(self, workspace_id: UUID, slot: str, submitted: str, request_id: UUID | None, member_id: UUID, merge: Callable[[str | None, str], str] | None) -> None
```

**Purpose**: Writes a credential submitted through a sealed member prompt, but only if the member is currently a seated workspace admin. It also makes prompt fulfillment one-time when a request ID is present.

**Data flow**: It receives the workspace, slot, submitted secret, optional request ID, member ID, and optional merge function. It locks the workspace row, verifies the member's admin authority, optionally records that this request-slot pair has been claimed, decrypts any current value, merges or replaces it, encrypts the final value, and inserts or updates the credential row.

**Call relations**: It uses `workspace_tx` and SQLAlchemy select/insert/update calls for the guarded database work. When authority is missing or the request was already used, it raises `CredentialRequestInvalid` instead of writing the secret.

*Call graph*: 5 external calls (__init__, insert, select, update, workspace_tx).


##### `CredentialStore.clear`  (lines 296–305)

```
async def clear(self, workspace_id: UUID, slot: str) -> None
```

**Purpose**: Deletes a stored credential slot for a workspace. It treats an already-empty slot as successfully cleared, which makes repeated disconnect or cleanup actions safe.

**Data flow**: It receives a workspace ID and slot name. It opens a database transaction and deletes any matching credential row. It returns nothing and does not complain if no row existed.

**Call relations**: It uses `workspace_tx` for the transaction and SQLAlchemy delete for the database change. No caller is shown in the provided graph.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `CredentialStore.update`  (lines 307–354)

```
async def update(self, workspace_id: UUID, slot: str, submitted: str, merge: Callable[[str | None, str], str]) -> None
```

**Purpose**: Merges a private submitted value into an existing encrypted credential slot. This is for structured secrets where a new submission updates part of the stored value rather than replacing it blindly.

**Data flow**: It receives a workspace ID, slot, submitted value, and merge function. It rejects an empty submission, locks the workspace for writing, decrypts the current credential if present, calls the merge function with current and submitted values, rejects an empty merged result, encrypts the result, and inserts or updates the database row.

**Call relations**: It uses `workspace_tx` and SQLAlchemy select/insert/update statements. The caller supplies the merge rule, while this method supplies the safe encrypted database boundary around that rule.

*Call graph*: 4 external calls (insert, select, update, workspace_tx).


##### `CredentialStore.get`  (lines 356–368)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Fetches and decrypts the stored secret for one workspace slot. It raises a specific missing-slot error when no credential has been saved.

**Data flow**: It receives a workspace ID and slot name. It reads the encrypted row from the credential table, raises `CredentialSlotUnset` if none exists, decrypts the ciphertext, and returns the plaintext secret.

**Call relations**: This is called by sandbox environment setup, `credential_host`, and egress-rule derivation when they need the real stored value. It uses `workspace_tx` and SQLAlchemy select to read the database before decrypting.

*Call graph*: called by 3 (_keyed_provider_env, credential_host, derive_credential_rules); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 370–401)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a credential only if the stored value is still the value the caller expected. This protects refreshed OAuth-style tokens from overwriting each other when two refreshes happen at the same time.

**Data flow**: It receives a workspace ID, slot, expected current plaintext, and new plaintext. It rejects an empty new value, reads and decrypts the stored credential, compares it with the expected value, and if they match, updates the row with the encrypted new value. It returns `true` only when exactly one row was updated.

**Call relations**: It uses `workspace_tx` and SQLAlchemy select/update statements. It does not create the first credential value; it is meant for later safe rotation after a credential already exists.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 425–430)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host choice declaration is internally valid as soon as it is created. The default host must be one of the declared allowed hosts.

**Data flow**: It reads the new `HostChoice` object's default and allowed host list. If the default is missing from the list, it raises a value error; otherwise, the object remains usable.

**Call relations**: This runs automatically after a `HostChoice` dataclass is created. It protects later code, including `credential_host`, from working with an impossible host-choice setup.


##### `HostChoice.resolve`  (lines 432–437)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string. It accepts different capitalization from the stored value but never returns free-form user text.

**Data flow**: It receives the selected text, trims spaces, lowercases it for comparison, and searches the declared host list. It returns the matching declared host literal, or `null` if the selection is not allowed.

**Call relations**: `credential_host` uses this after reading a workspace's selected host from the credential store. This keeps proxy-facing host values limited to the safe list declared in code.


##### `credential_host`  (lines 440–457)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Finds the provider host that should be used for a credential in a workspace. The host may be fixed by code, or it may come from a controlled `HostChoice` with a safe default.

**Data flow**: It receives a credential store, workspace ID, and either a plain host string or a `HostChoice`. If given a string, it returns it unchanged. If given a `HostChoice`, it tries to read the workspace's selected value from the encrypted store, falls back to the declared default when unset, and returns the resolved allowed host or `null` for an invalid stored choice.

**Call relations**: It calls `CredentialStore.get` when a host choice depends on a stored workspace selection. The same answer can be used by sandbox environment setup and egress proxy rules, so both sides agree on where the credential is allowed to go.

*Call graph*: calls 1 internal fn (get).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `sync job authentication`

Some data sources cannot get credentials through the normal installed-account flow, or a deployment may choose to keep an API key itself. In that case, the member adds a key directly. This file is the small bridge that turns that stored key into the credential a sync job can use.

The important safety rule is that the secret stays on the host side. The sync job runs in a trusted jobs role, reads the encrypted credential through `CredentialAccess` (a controlled interface for reading only approved credential slots), and uses it to authenticate HTTP requests to the outside provider. The key is not passed into the sandbox or exposed to an agent.

`DirectAuthProxy` is the single object here. It is given access to the workspace’s credentials. When asked for a credential, it ignores the account handle as a source of secret data and instead looks up the credential slot named after the connector’s provider. It then wraps the retrieved secret as a bearer token, meaning an HTTP-style token used to prove permission to the provider. In everyday terms, this file is like a locked key cabinet clerk: given the provider name, it fetches the right key and hands back only the form the sync job needs.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Fetches the API key for a provider from the workspace credential store and returns it as a bearer credential. A sync job uses this when a source is routed through the direct bring-your-own-key authentication path.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. It uses the provider name to read the matching stored secret through `self.credentials.get(...)`, then wraps that secret in a `Credential` object as its bearer token. The result is returned to the caller; the credential store is read but not changed.

**Call relations**: When the direct auth proxy is asked to supply authentication for a source run, this method does the actual lookup. After reading the provider-specific secret, it hands the value to `Credential.__init__` so the rest of the sync code can treat it as a normal bearer credential without learning how it was stored.

*Call graph*: 1 external calls (__init__).


### `extensions/web/ufo_ext_web/anthropic_login.py`

`domain_logic` · `request handling`

This file is the Anthropic sign-in helper for the web extension. Its job is to turn a human sign-in step into a credential the system can safely store for that member. If the deployment has an Anthropic OAuth client, it creates a special authorization link that sends the user to Anthropic. Anthropic then shows the user a code, and the user pastes that code back into this system. If there is no OAuth setup elsewhere, the user can instead paste an API key they created in Anthropic’s console.

The important safety idea is that the system does not trust a pasted value just because it looks plausible. For OAuth, it creates a one-time secret called a verifier, sends a matching challenge to Anthropic, and later checks that the returned state still matches the browser’s saved verifier. This is like giving someone half of a claim ticket and only accepting the coat when both halves match. It then exchanges the code for an access token.

Finally, `verified_key` checks the credential by asking Anthropic’s models endpoint. OAuth access tokens and API keys need different HTTP headers, so the file chooses the right style. If Anthropic does not answer successfully, the credential is treated as invalid and should not be saved.

#### Function details

##### `AnthropicCodeLogin.authorize`  (lines 73–96)

```
def authorize(self) -> PendingAuthorization
```

**Purpose**: Starts the Anthropic OAuth sign-in flow. It builds the Anthropic authorization URL the user should open, and creates a browser cookie value that will later prove the pasted code belongs to the same sign-in attempt.

**Data flow**: It starts with the configured Anthropic client ID and URLs. It creates a random verifier, turns it into a hashed challenge, places the challenge and other OAuth details into a query string, and returns a `PendingAuthorization` containing the finished Anthropic link plus the verifier to save in the user’s browser cookie.

**Call relations**: This is used at the beginning of the web sign-in journey, before the user visits Anthropic. It relies on standard helpers for random bytes, base64 text, hashing, and URL query building, then hands the surrounding web flow the link to show and the cookie value to remember.

*Call graph*: 5 external calls (__init__, urlsafe_b64encode, sha256, token_bytes, urlencode).


##### `AnthropicCodeLogin.claim`  (lines 98–132)

```
async def claim(self, pasted: str, verifier: str) -> Grant | None
```

**Purpose**: Redeems the code the user pasted after visiting Anthropic. If the code and saved verifier match, it asks Anthropic for an access token and returns that token as a grant; otherwise it returns nothing.

**Data flow**: It receives the pasted text from the user and the verifier previously saved in the browser. It first extracts the actual code and optional state from the pasted text, rejects missing or mismatched values, then sends a JSON request to Anthropic’s token endpoint. If Anthropic replies successfully and the response can be understood as a grant, that grant comes out; network errors, bad status codes, or malformed replies all become `None`.

**Call relations**: This runs after `authorize`, when the user has copied something back from Anthropic. It hands pasted text to `_split_pasted` so the user can paste a bare code, a `code#state` pair, or a full callback URL. It then uses an HTTP client to contact Anthropic and passes the successful response through `granted`, which turns Anthropic’s token response into the project’s grant object.

*Call graph*: calls 1 internal fn (_split_pasted); 4 external calls (AsyncClient, dumps, compare_digest, granted).


##### `_split_pasted`  (lines 135–147)

```
def _split_pasted(pasted: str) -> tuple[str, str | None]
```

**Purpose**: Pulls the authorization code, and possibly the returned state, out of whatever form the user pasted. This makes the paste step forgiving instead of forcing users to copy one exact substring.

**Data flow**: It receives raw pasted text and trims extra spaces. If the text looks like a URL, it reads `code` and `state` values from the URL’s query string or fragment. If the text contains `#`, it treats the part before `#` as the code and the part after it as the state. Otherwise, it treats the whole trimmed text as the code and reports no state.

**Call relations**: This is a helper used by `AnthropicCodeLogin.claim` before any token request is made. Its output decides whether `claim` has a usable code and whether the returned state matches the verifier saved from the earlier authorization step.

*Call graph*: called by 1 (claim); 2 external calls (parse_qs, urlsplit).


##### `verified_key`  (lines 150–164)

```
async def verified_key(credential: str) -> bool
```

**Purpose**: Checks whether an Anthropic credential really works before it is stored. It accepts either an OAuth access token or a normal Anthropic API key and asks Anthropic for a simple confirmation by listing models.

**Data flow**: It receives one credential string. If the credential looks like an Anthropic OAuth access token, it sends it as a bearer token with Anthropic’s OAuth beta header; otherwise it sends it as an `x-api-key`. It makes a request to Anthropic’s models endpoint and returns `true` only when Anthropic replies with success; network failures or rejected credentials return `false`.

**Call relations**: This function is used after a user provides or obtains a credential, as a final gate before saving it. It directly contacts Anthropic with the right header style for the credential type, so the rest of the system can treat a `true` result as evidence that the credential is usable.

*Call graph*: 1 external calls (AsyncClient).


### `extensions/web/ufo_ext_web/openai_login.py`

`io_transport` · `sign-in request handling`

This file solves a practical sign-in problem: the portal needs a member-owned OpenAI credential before it can let that member use OpenAI-backed features. Instead of asking the user to paste a secret key, it starts a device sign-in, which is like the login flow used by smart TVs: the app shows a short code, and the user types that code into an OpenAI page in their browser.

The file defines small result objects for the two main stages. A device authorization contains the code the user sees, the hidden id OpenAI uses to track the login, the web address where the code is entered, and how often the app should check back. A device claim says whether the user is still approving, whether a usable key was received, or whether the process failed in a way the user should see.

The central class, OpenAiDeviceLogin, talks to OpenAI over HTTP. First it asks OpenAI to open a device login and returns the code to display. Later it polls OpenAI to see whether the member approved. If approval has happened, it receives a short-lived authorization code and a verifier, then trades them at OpenAI’s token endpoint for the real stored credential. It also checks that the returned token really belongs to a ChatGPT account before accepting it. If OpenAI says “not yet,” the file reports that as pending rather than failure.

#### Function details

##### `OpenAiDeviceLogin.request_code`  (lines 85–103)

```
async def request_code(self) -> DeviceAuthorization | None
```

**Purpose**: Starts the OpenAI device sign-in by asking OpenAI for a user-facing code. The portal uses this before drawing the sign-in page, so the user immediately sees the code they need to type into OpenAI.

**Data flow**: It starts with the configured OpenAI client id and the URL for requesting a device code. It sends those to OpenAI, checks that the reply is successful, reads the hidden device id and visible user code, and turns the polling interval into a safe number. It returns a DeviceAuthorization when everything needed is present, or None if OpenAI cannot start the sign-in or sends an unusable answer.

**Call relations**: This is the first step in the login story. It creates the information later needed by OpenAiDeviceLogin.claim: the hidden device authorization id and the visible user code. It relies on _interval to make OpenAI’s suggested wait time usable.

*Call graph*: calls 1 internal fn (_interval); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin.claim`  (lines 105–126)

```
async def claim(self, device_auth_id: str, user_code: str) -> DeviceClaim
```

**Purpose**: Checks whether the user has approved the device sign-in, and if they have, continues the exchange until a stored credential can be returned. Someone would use this repeatedly while the sign-in page is waiting.

**Data flow**: It receives the hidden device authorization id and the user code from the earlier request. It sends both back to OpenAI to ask whether approval has happened. If OpenAI says the user is not done, it returns a pending DeviceClaim. If OpenAI returns an authorization code and verifier, it passes those to _redeem. If those pieces are missing, it returns a refused DeviceClaim with a user-readable failure reason.

**Call relations**: This is the polling step after request_code. When OpenAI has not approved the login yet, it delegates the decision to _unapproved. When OpenAI has approved it, it hands the authorization code and verifier to OpenAiDeviceLogin._redeem to buy the final credential.

*Call graph*: calls 2 internal fn (_redeem, _unapproved); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin._redeem`  (lines 128–153)

```
async def _redeem(self, client: httpx.AsyncClient, code: str, verifier: str) -> DeviceClaim
```

**Purpose**: Trades an approved sign-in grant for the actual credential the workspace can store. This is the final, stricter step that proves the approval can become a usable ChatGPT/Codex token.

**Data flow**: It receives an HTTP client, an authorization code, and a code verifier. It sends them to OpenAI’s normal token endpoint using form data, then parses the token response. It rejects the response if the HTTP call fails, the token shape is invalid, or the access token does not identify a ChatGPT account. If all checks pass, it returns a granted DeviceClaim containing the stored form of the credential.

**Call relations**: OpenAiDeviceLogin.claim calls this only after OpenAI says the user approved the device sign-in. This function then calls the shared token parsing helpers, granted and chatgpt_account_id, so the rest of the app receives only a credential that looks valid for the expected OpenAI account type.

*Call graph*: called by 1 (claim); 4 external calls (__init__, post, chatgpt_account_id, granted).


##### `_interval`  (lines 156–162)

```
def _interval(raw: object) -> int
```

**Purpose**: Turns OpenAI’s suggested polling delay into an integer number of seconds. It protects the app from polling too fast if OpenAI omits the value or sends it in an unexpected form.

**Data flow**: It receives any raw value from OpenAI’s response. It tries to convert that value into an integer after treating it like text. If conversion works, that number comes out; if not, the default polling interval comes out instead.

**Call relations**: OpenAiDeviceLogin.request_code uses this when building the DeviceAuthorization returned to the sign-in page. That returned interval tells the waiting page or caller how long to pause between checks.

*Call graph*: called by 1 (request_code).


##### `_unapproved`  (lines 165–177)

```
def _unapproved(polled: httpx.Response) -> DeviceClaim
```

**Purpose**: Interprets a non-successful poll response from OpenAI. It separates “the user has not approved yet” from “the device sign-in cannot continue.”

**Data flow**: It receives the HTTP response from a poll attempt that did not return success. It first checks status codes that OpenAI uses while approval is still pending. If needed, it reads OpenAI’s error body and looks for pending-style error codes. It returns a pending DeviceClaim for still-waiting cases, or a refused DeviceClaim with a restart message for other failures.

**Call relations**: OpenAiDeviceLogin.claim calls this when polling OpenAI does not return a normal success. This keeps the main claim flow simple: pending answers keep the login page waiting, while true failures are reported back for the user to read.

*Call graph*: called by 1 (claim); 2 external calls (__init__, json).


### Messaging setup actions
Provides member-facing setup actions for connecting iMessage numbers and Slack workspaces to UFO.

### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file solves a practical safety problem: the system cannot just start texting someone from an iMessage line. The phone owner must prove they control the number and send the first opt-in message. Without this file, a workspace would have no guided way to connect a member’s phone number to the iMessage provider.

The flow starts with a phone number supplied by the user. The input model cleans it up and only accepts valid US numbers, turning formats like “415-555-0123” into the standard form “+14155550123”. The main tool then checks that the request comes from a signed-in member, that an iMessage provider is configured, and that the workspace is bound to that provider. If the workspace is not yet bound, only an admin can do that first connection.

Next, it reserves the requested phone number for the member. If another member already owns it, the tool stops. If it is already linked to this member, it simply returns the assigned line to text. Otherwise, it creates or reuses a pending claim: an assigned provider phone number plus a random opt-in code. It stores that pending claim so repeated requests do not create conflicting setup attempts. Finally, it shares a QR code artifact and returns clear instructions, including a deep link, so the member can send the required opt-in text from their phone.

#### Function details

##### `opt_in_link`  (lines 37–41)

```
def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str
```

**Purpose**: Builds a clickable phone message link that opens the Messages app with the opt-in text already filled in. This saves the member from manually typing the special code.

**Data flow**: It receives the assigned provider phone number and the opt-in code. It combines them with the fixed opt-in phrase, safely encodes the message text for use inside a link, and returns an `sms:` link ready to show to the user.

**Call relations**: When the tool prepares the pending opt-in response, `_opt_in_result` asks this function for the link. The link is then included in the tool result alongside the plain text instructions.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `opt_in_qr`  (lines 44–52)

```
def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes
```

**Purpose**: Creates a QR code image for the same opt-in message. This is useful when the member is reading instructions on a desktop computer and wants to scan them with their phone instead of copying a code by hand.

**Data flow**: It receives the assigned provider phone number and opt-in code. It builds a phone-readable SMS payload, turns it into a PNG QR code image in memory, and returns the image bytes.

**Call relations**: During a new pending phone claim, `ImessageConnect.run` calls this function just before sharing an artifact with the user. The QR image is attached to the conversation so the member can scan it and open Messages on their phone.

*Call graph*: called by 1 (run); 2 external calls (BytesIO, make).


##### `_display_phone`  (lines 58–62)

```
def _display_phone(phone_number: str) -> str
```

**Purpose**: Turns a US phone number in strict international form into a friendlier display format. For example, it can show `+14155550123` as `(415) 555-0123`.

**Data flow**: It receives a phone number string. If it matches the expected US number pattern, it extracts the area code and local parts and returns a readable version; otherwise, it returns the original string unchanged.

**Call relations**: Both `ImessageConnect.run` and `_opt_in_result` use this when writing instructions for people. It keeps internal phone-number storage precise while making user-facing messages easier to read.

*Call graph*: called by 2 (run, _opt_in_result).


##### `ImessageConnectInput._e164`  (lines 73–85)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Checks and normalizes the phone number entered by the user. It accepts common US formatting but rejects letters, invalid area codes, and non-US-style numbers.

**Data flow**: It receives the raw phone number text from the tool input. It trims spaces, removes punctuation and other non-digit formatting, handles an optional leading US country code, validates the final number, and returns it in standard `+1...` form. If the value is not acceptable, it raises a clear validation error.

**Call relations**: This validator runs as part of building `ImessageConnectInput`, before the main tool logic uses the phone number. That means `ImessageConnect.run` can work with one consistent phone-number format instead of defending against many user-entered variations.


##### `_result`  (lines 88–94)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Packages a tool response into the format expected by the broader system. It returns a small JSON message that tells the caller the connection state and what the user should do next.

**Data flow**: It receives a state, an instruction message, and any extra fields such as phone numbers or links. It turns those values into JSON text, wraps that text in a content object, and returns a tool result marked as untrusted user-facing output.

**Call relations**: The main connection flow uses this helper whenever it needs to stop with a status such as not connected, pending, or connected. `_opt_in_result` also uses it to build the richer pending response.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 97–106)

```
def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult
```

**Purpose**: Builds the response shown when a phone number claim is waiting for the member to send the opt-in text. It explains exactly what to text, where to send it, and includes a link for convenience.

**Data flow**: It receives the assigned provider phone number and the random opt-in code. It creates the full opt-in message, formats the assigned phone number for readability, builds a clickable opt-in link, and returns a pending tool result with all of that information included.

**Call relations**: After `ImessageConnect.run` creates or finds a pending claim and shares the QR code, it calls this function to produce the final instruction message. This function relies on `_display_phone`, `opt_in_link`, and `_result` to assemble the user-facing response.

*Call graph*: calls 3 internal fn (_display_phone, _result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 113–168)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full iMessage connection process for one member and one phone number. It checks who is asking, connects the workspace to the provider if allowed, reserves the phone number, and starts or completes the opt-in setup flow.

**Data flow**: It receives the tool context, which includes workspace, member, installation, storage, and artifact-sharing access, plus the already-validated phone number input. It checks that a signed-in member is present, loads the provider, verifies or creates the provider binding, reserves the phone number for the member, and then either reports that the number is taken, confirms an already linked number, or creates/reuses a pending claim. For a pending claim, it stores the assigned line and opt-in code, shares a QR code image, and returns instructions for sending the opt-in text.

**Call relations**: This is the central function called when the iMessage connection tool is invoked. It delegates small pieces of user-facing output to `_result`, `_opt_in_result`, `_display_phone`, and `opt_in_qr`, while relying on the surrounding tool context for permission checks, installation binding, address reservation, storage, and artifact sharing.

*Call graph*: calls 6 internal fn (share_artifact, speaker_is_admin, _display_phone, _opt_in_result, _result, opt_in_qr); 6 external calls (__init__, now, choice, claim_key, read_claim, uuid4).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack conversation lookup during request handling`

This file turns Slack setup into a set of tools the agent can offer in conversation. Without it, an admin would have no guided path to connect Slack, and the agent would not be able to discover Slack channels or direct messages by name.

There are two setup paths. The preferred path is OAuth, which is the familiar “Add to Slack” button. If the UFO deployment has its own Slack app configured, the file creates a short-lived install link for an admin. The alternative path is “manifest”, meaning the customer creates their own Slack app from a generated YAML recipe. In that path, sensitive secrets like the bot token and signing secret are collected through private credential slots, not through chat.

Both paths end in the same place: UFO proves which Slack workspace and bot it is connected to, binds that Slack team to this UFO workspace, and waits until Slack sends a properly signed request. That final signed request is the proof that Slack can reach this deployment, like a doorbell test after wiring a new intercom.

The file also provides a runtime search tool, `slack_channels`, which uses the bot token to page through Slack conversations and return matching channels, group chats, or direct messages. Because Slack conversation names and topics are user-authored text, the result is marked as untrusted.

#### Function details

##### `_events_url`  (lines 145–146)

```
def _events_url(public_base_url: str) -> str
```

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 149–151)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 154–195)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 198–228)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 231–265)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 268–292)

```
async def _verified(ctx: ToolContext) -> bool
```

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, mirror_url_verified, verifying_fingerprint).


##### `slack_manifest_handler`  (lines 295–310)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 313–336)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 339–345)

```
def _token_diagnosis(error: str) -> str
```

*Call graph*: called by 1 (_derive_manifest_identity).

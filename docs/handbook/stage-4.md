# Workspace onboarding, member identity, and account connection setup  `stage-4`

This stage covers the “getting connected” part of the system. It is used during first setup, signup, and later when a member links outside services. The main onboarding file creates a real workspace, adds the first admin, creates the main assistant, checks required secrets, and lets extensions run their own setup. The onboarding control API is the guarded doorway used by the Rust control plane, so rules about seats, ownership, credits, invitations, and prompts stay consistent.

Once a workspace exists, provisioning turns agents supplied by extensions into real workspace agents without overwriting user changes. Agent setup checks what each agent still needs, such as a linked account, password-like credential, or schedule. Grants records which member owns an outside account connection and which agent may use it.

Connection files are the bridges to specific services. Composio sends users through a hosted approval page and back. GitHub verifies the authorized installation really belongs to the user. iMessage reserves a phone number and gives opt-in instructions. The seed file adds a demo “Kitchen sink” conversation so new users can see the portal in action.

## Files in this stage

### First-run workspace bootstrap
The initial onboarding orchestrator creates the permanent workspace, first admin, main assistant, required secrets checks, and extension setup handoff.

### `core/src/ufo/onboard/onboarding.py`

`orchestration` · `startup / first-run initialization`

This file is the “new office setup” checklist for UFO. On a cold start, the system needs a workspace to live in, a first human administrator, and a default agent before anyone can use it. This file creates those core records once, and refuses to run again if the workspace already has a member, so it does not accidentally create a second starting world.

Before touching the database, it checks that the chosen AI model has the needed environment secret, such as an API key. It also checks whether extension onboarding steps need a credential store. This matters because a failed setup after partly creating a workspace would leave the system in an awkward half-ready state.

The main `Onboarding` class coordinates the flow. First it validates required keys, then creates the workspace, admin member, and main agent inside one database transaction. After that core setup is safe, it runs provisioning and onboarding hooks supplied by installed extensions. Extension failures are logged and skipped, rather than breaking the already-created workspace or stopping other extensions. In other words, the core house is built first; optional add-ons are invited in afterward.

#### Function details

##### `run_onboarding_steps`  (lines 51–79)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs setup steps contributed by installed extensions for a newly created workspace. It gives each extension its own scoped context, so the extension sees only the credential slots it declared.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, checks each manifest for onboarding steps, builds an extension-specific context when credentials are available, and awaits each step. It does not return data; its visible effects are whatever the extension steps do, plus log messages when steps are skipped or fail.

**Call relations**: After the core workspace exists, `Onboarding.run_steps` calls this function to let extensions perform their own setup. It asks `context_for` for the right extension context, uses `ws` to mark which workspace the steps belong to, and sends problems to `log` so one broken extension does not stop the rest.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 95–98)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-time onboarding sequence from start to finish. This is the high-level method a caller uses when it wants both the core workspace and extension setup completed.

**Data flow**: It starts with the `Onboarding` object’s stored configuration, email, model choice, credential store, and manifests. It first calls `create` to build the durable core workspace, then passes the resulting workspace and member identity to `run_steps`. It returns the `Onboarded` result so the caller knows which workspace and first member were created.

**Call relations**: This method is the top-level story for this file. It delegates the careful core creation to `Onboarding.create`, then delegates add-on setup to `Onboarding.run_steps`, keeping the public flow simple and ordered.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 100–106)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates only the essential, durable core of a new installation: required key checks, workspace, first admin, and main agent. It deliberately leaves extension onboarding for later so optional add-ons cannot prevent the core setup from being recorded once it starts.

**Data flow**: It uses the onboarding settings already stored on the object. First it checks whether the selected model needs an environment key, then checks whether extension steps require a credential store, and only then opens the path to database creation. It returns an `Onboarded` value containing the new workspace ID and admin member ID.

**Call relations**: `Onboarding.run` calls this first. Inside, it calls `_require_model_key`, `_require_credentials_for_steps`, and `_create_workspace` in that order, so validation happens before any database rows are written.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 108–110)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the post-creation setup that depends on a workspace already existing. This includes agent provisioning from extensions and then extension onboarding hooks.

**Data flow**: It receives an `Onboarded` result with the new workspace ID. It applies agent provisioning based on the installed manifests, then passes the manifests, workspace ID, and credential store into the extension onboarding runner. It returns nothing, but may create or configure extension-provided resources.

**Call relations**: `Onboarding.run` calls this after `create` succeeds. It first uses `AgentProvisioning` for extension-provided agent setup, then hands off to `run_onboarding_steps` so each extension can run its own declared first-run work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 112–123)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Checks that a credential store is available when installed extensions have onboarding steps that may need to save per-workspace secrets. This prevents creating a workspace that cannot finish required extension setup.

**Data flow**: It reads the `credentials`, `manifests`, and credential-key environment name from the object’s configuration. If credentials are present, it allows setup to continue. If credentials are missing and any extension declares onboarding steps, it raises an error before any database changes happen.

**Call relations**: `Onboarding.create` calls this during the preflight checks. It does not call other project functions; it acts as a gate that stops the first-run flow early when extension setup would be unable to use the credential system.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 125–133)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks whether the chosen AI model needs an environment variable secret before the first conversation can work. If the required key is missing, it stops onboarding with a clear error.

**Data flow**: It asks `_model_key_env` for the name of the required environment variable, if there is one. If no key is required or the key is present in the deployment environment, it lets setup continue. If the key is missing, it raises an error explaining which variable must be set.

**Call relations**: `Onboarding.create` calls this before database creation. It relies on `_model_key_env` to learn what to check, then uses `deploy_env` to read the deployment environment in the accepted ways.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create); 1 external calls (deploy_env).


##### `Onboarding._model_key_env`  (lines 135–138)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name needed for the selected model’s API key, when the core model registry knows one. If a model provider comes from an extension and resolves its key later, this may return nothing.

**Data flow**: It reads the configuration, installed manifests, and selected model from the object. It builds or consults the model registry and asks what environment key is associated with that model. It returns the environment variable name, or `None` if there is no eager key check to perform.

**Call relations**: `Onboarding._require_model_key` calls this as its lookup step. This function hands the model-key decision to `model_registry`, which knows about both built-in models and extension-contributed model providers.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 140–167)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the new workspace, first admin member, and main agent to the database in one protected transaction. It also prevents duplicate initialization by refusing to run if any member already exists.

**Data flow**: It opens a workspace database transaction, checks whether the member table already has a row, and raises `AlreadyInitialized` if so. Otherwise it creates new IDs, inserts a workspace row, creates the first admin member using the provided email, inserts the default main agent with the selected model and reasoning setting, and returns an `Onboarded` record with the new workspace and member IDs.

**Call relations**: `Onboarding.create` calls this only after required keys have been checked. It uses `workspace_tx` for the database boundary, SQLAlchemy helpers to select and insert rows, `create_member` to add the first admin consistently with the seating system, and `uuid4` to give the workspace and agent fresh identities.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Account grant foundations
Hosted OAuth callbacks and UFO grant records connect outside accounts to workspace members and authorized agents.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `connect request handling`

OAuth is the web pattern where a user approves access to an outside service without giving this app their password. In this project, Composio runs much of that consent flow, but ufo expects a more traditional two-step shape: first make an authorization URL, then exchange a returned code for an account. This file adapts one shape to the other.

The main class, ComposioOAuthProvider, describes one Composio-backed provider. Its authorization URL does not go straight to Composio. Instead, it sends the browser to this extension’s own oauth route. That route can do asynchronous work, which is needed because creating a Composio connect link requires an API call.

The oauth_route function is the bridge. On the first visit, it asks Composio for a consent link for the current workspace and redirects the browser there. After the user approves or cancels, Composio sends the browser back to the same route. If approval succeeded, the route forwards the connected account id to ufo’s normal callback as if it were an OAuth code. Later, exchange verifies with Composio that this account belongs to the expected workspace user before returning an OAuthAccount. The actual secret token stays inside Composio, so this extension never stores provider credentials.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the URL where the user’s browser should start the connection process. Instead of sending the browser directly to Composio, it points to this extension’s bridge route so the route can create the real Composio consent link.

**Data flow**: It receives the protected state value and the final callback URL from ufo. It keeps the provider name, state, and callback as query parameters, extracts the origin from the callback URL, and returns a full URL to the extension’s OAuth bridge route.

**Call relations**: When ufo’s connect flow needs a place to send the browser, this method supplies that starting URL. It uses _origin to make sure the callback has a usable scheme and host, then hands the browser off to oauth_route by encoding the needed information into the URL.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected account id into ufo’s OAuthAccount record. It also checks that the account belongs to the expected workspace-scoped Composio user, which prevents someone from injecting an unrelated account id.

**Data flow**: It receives the returned code, which in this bridge is really a Composio connected account id, plus the workspace id. It builds the expected Composio user id for that workspace, asks the Composio client to confirm the account and provider, tries to fetch a friendly label, and returns an OAuthAccount with the account id and optional label. It does not read or store any provider token.

**Call relations**: After oauth_route forwards a successful return to ufo’s normal callback, the core connect flow calls this exchange step. This function talks to the Composio client to validate the account, then hands ufo the small account record it needs to bind the connector.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge between ufo and Composio for both halves of the consent trip. It starts consent by redirecting the user to Composio, and it finishes consent by redirecting back to ufo’s core callback with the connected account id.

**Data flow**: It reads query parameters from the incoming request, especially state and callback. If either is missing, it returns an error response. If Composio has returned a connected account id, it redirects to the callback with that id as the code. If Composio returned a failure status without an account id, it returns a clear failure message. Otherwise, it reads the requested provider, builds a return URL back to itself, asks Composio for a consent link for the current workspace user, and redirects the browser to that link.

**Call relations**: ComposioOAuthProvider.authorize_url sends the browser here at the start. On the start leg, oauth_route calls the Composio client to create the hosted consent link. On the return leg, it sends the browser onward to ufo’s callback, which later leads to ComposioOAuthProvider.exchange. It uses _origin when building safe absolute bridge URLs.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the scheme and host part of a URL, such as turning https://example.com/path into https://example.com. This is needed so redirect URLs can be built against the same site that ufo is using.

**Data flow**: It receives a URL string, parses it, checks that it has either http or https plus a host name, and returns only the scheme and host. If the URL is incomplete or uses an unsupported scheme, it raises an error instead of building an unsafe or broken redirect.

**Call relations**: ComposioOAuthProvider.authorize_url uses this helper to place the bridge route on the same origin as the callback. oauth_route uses it when building the URL that Composio should return to. Keeping this check in one helper makes both redirect-building paths follow the same safety rule.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `core/src/ufo/runtime/access/grants.py`

`domain_logic` · `request handling, OAuth callback, permission changes`

This file is the project’s permission desk for external service accounts. A member may connect an account through OAuth, which is the common “sign in and approve access” flow used by many web services. The system then keeps the real token on the server side and gives agents only a safe grant record, not the secret itself.

The main path has two halves. `ConnectFlow.authorize` creates a provider approval link and hides important facts inside a sealed `state` value: the workspace, member, agent, provider, conversation, and turn that asked for the connection. Later, `ConnectFlow.complete` opens that sealed state, exchanges the returned code with the provider, records or reuses the connection, grants the intended agent, notifies extension hooks, and optionally posts a message back into the conversation.

`GrantStore` is the database-facing part. It creates connection rows, creates grant edges from connections to agents, checks whether a member is allowed to change a connection, revokes grants, shares or unshares connections, and disconnects accounts cleanly. It also wakes parked feed sources after a reconnect, so a member who fixed permissions does not wait for a slow retry cycle.

A useful analogy is a library card system: the connected account is the card owned by a member, and each grant is a note saying which agent may borrow through that card. Without this file, agents could not safely use member-approved outside accounts, and the system would have no reliable record of ownership, sharing, or revocation.

#### Function details

##### `grant_sentinel`  (lines 49–53)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a special placeholder credential string for a connected account. The sandbox and the outgoing proxy can both recognize this same placeholder without sharing the real secret.

**Data flow**: It takes an account id as text, adds the fixed grant prefix in front of it, and returns the combined sentinel string. It does not read or change stored data.

**Call relations**: Other parts of the system can use this deterministic value when they need a harmless stand-in for a real account credential. It is independent helper logic used wherever a grant must be represented safely.


##### `UnknownProvider.__init__`  (lines 61–62)

```
def __init__(self, provider: str) -> None
```

**Purpose**: Creates a clear user-facing error when someone asks for a connector provider that is not installed or recognized. The message names the missing provider in a sentence rather than exposing a bare internal slug.

**Data flow**: It receives the provider name, formats it into an explanatory error message, and initializes the lookup error with that message.

**Call relations**: Provider lookup paths call this when a provider cannot be found. `ConnectFlow._provider` uses it during actual authorization or completion, and `ConnectFlow.validate_provider` uses it when checking a requested provider up front.

*Call graph*: called by 2 (_provider, validate_provider).


##### `OAuthProvider.provider`  (lines 105–105)

```
def provider(self) -> str
```

**Purpose**: Defines the provider’s stable internal name, such as the connector slug the system stores in the database. Implementations supply this value.

**Data flow**: An implementing provider object returns its provider name. This protocol method itself is only a contract and does not compute anything here.

**Call relations**: The connect flow reads this from a provider descriptor after a successful OAuth exchange so the recorded connection uses the provider’s canonical name.


##### `OAuthProvider.host`  (lines 108–108)

```
def host(self) -> str
```

**Purpose**: Defines the network host that a grant permits the system to reach for this provider. This helps the outgoing proxy know which external destination belongs to the grant.

**Data flow**: An implementing provider object returns its host string. This protocol method is a promise that concrete providers must fulfill.

**Call relations**: After OAuth completion, `ConnectFlow.complete` passes this value into `GrantStore.record` so the saved grant can later guide controlled outgoing access.


##### `OAuthProvider.authorize_url`  (lines 110–110)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the web link that sends a member to the provider’s approval page. It includes sealed state so the callback can prove what request this approval belongs to.

**Data flow**: It receives the sealed state string and the callback address, then returns a URL for the member’s browser. Concrete provider implementations decide the exact URL format.

**Call relations**: `ConnectFlow.authorize` calls this after sealing the request details. The returned URL is shown to the member or stored for the pending connect request.


##### `OAuthProvider.exchange`  (lines 112–114)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the short-lived OAuth code returned by the provider into the system’s stable connected-account identity. The real token stays with the broker or provider-side server code rather than crossing into this grant layer.

**Data flow**: It receives the returned code, callback address, workspace id, and state value. It verifies and exchanges those with the provider, then returns an `OAuthAccount` containing the account id and optional display label.

**Call relations**: `ConnectFlow.complete` calls this during the OAuth callback. The returned account is then written into the grant database by `GrantStore.record`.


##### `OAuthProviderResolver.claims`  (lines 125–125)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open connector namespace can serve a provider slug that was not explicitly registered. This lets a broker support many provider names without listing each one in advance.

**Data flow**: It receives a provider name, may consult an external catalog or live broker state, and returns true if that provider is supported. The protocol only describes the behavior.

**Call relations**: `ConnectFlow.validate_provider` calls this when a provider is not in the fixed provider map. A false result leads to an unknown-provider error.


##### `OAuthProviderResolver.descriptor`  (lines 127–127)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuth provider descriptor for a provider claimed by an open namespace. This gives the connect flow the same kind of object it would have received from a fixed provider registration.

**Data flow**: It receives a provider name and returns an `OAuthProvider` descriptor. The protocol leaves the construction details to the resolver implementation.

**Call relations**: `ConnectFlow._provider` calls this when the provider is not directly registered but a resolver is installed. The returned descriptor is then used for authorization and completion.


##### `ConnectionHooks.fire`  (lines 234–234)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Publishes a newly recorded connection to extension code that can derive extra state from it, such as feed source rows. It is a hook point: installed extensions decide what to do.

**Data flow**: It receives a `ConnectionRecorded` payload describing the connection, owner, provider, account, and agent. Implementations may create or update related state and return nothing.

**Call relations**: `ConnectFlow.complete` calls this after the connection grant has been saved. It lets extension work happen before the member reads the callback result.


##### `ConnectResumption.resume`  (lines 247–254)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Tells the conversation that originally asked for a connection that the account is now connected. This helps the agent continue without the member having to type another message.

**Data flow**: It receives the conversation id, message text, speaker member id, and an idempotency key, which is a repeat-safe key that prevents duplicate resume messages. It returns whether the resume message was accepted.

**Call relations**: `ConnectFlow.complete` calls this last, after the database record and connection hooks have finished. If resumption is not installed or fails, the grant still remains recorded.


##### `_resume_key`  (lines 257–272)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a stable duplicate-prevention key for one connect attempt. It uses the sealed OAuth state rather than the connection id, because several separate connect attempts may land on the same connection row.

**Data flow**: It takes the sealed state string, hashes it with SHA-256, keeps a short digest prefix, adds the connect-message prefix, and returns the resulting key. The sealed state itself is not stored as the key.

**Call relations**: `ConnectFlow.complete` uses this key when asking `ConnectResumption.resume` to post back into the conversation. If the browser callback is refreshed, the same state gives the same key, so the resume can be safely deduplicated.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 299–300)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently active in the request context. This keeps every grant and connection operation scoped to the right workspace.

**Data flow**: It reads the current workspace context and returns its workspace id. It does not accept arguments or change data.

**Call relations**: Most `GrantStore` database methods rely on this property while building queries. The surrounding code must have already entered the correct workspace context.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 303–304)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent id currently targeted by object dispatch. This is the agent that grant operations apply to when a command is aimed at an agent.

**Data flow**: It reads the current object-scope agent id and returns it. It does not write anything.

**Call relations**: `GrantStore.record`, `active_grants`, `attach`, and grant permission checks use this value to bind or inspect grants for the correct agent.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 306–470)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None, landed_turn_id: UUID | None=None) ->
```

**Purpose**: Saves a completed connection and grants the current agent access to it. It reuses an existing connection for the same workspace, provider, and account, but refuses to let a different member take ownership of that account.

**Data flow**: It receives provider and account details, the granting member, conversation, sharing choice, optional account label, and optional turn id. Inside one database transaction, it inserts or finds the connection, verifies ownership, updates metadata, inserts or refreshes the agent grant, marks the asking turn as landed if needed, and wakes related parked sources for retry. It returns the connection id that now holds the grant.

**Call relations**: `ConnectFlow.complete` calls this after the OAuth provider returns an account. Its result is then used to publish a `ConnectionRecorded` hook event and to build the final completion response.

*Call graph*: 9 external calls (__init__, now, and_, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 472–517)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the current agent’s usable connection grants, including who owns each connected account. This is the view needed when an agent is about to run with granted external access.

**Data flow**: It reads connector-grant, connection, and member rows for the current workspace and current agent. It turns each database row into a `Grant` object and returns them as a tuple.

**Call relations**: The sandbox environment builder calls this through `_grant_cli_env` to prepare safe credential placeholders for the agent’s execution environment.

*Call graph*: called by 1 (_grant_cli_env); 4 external calls (__init__, and_, select, workspace_tx).


##### `GrantStore.revoke`  (lines 519–531)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the acting member is allowed to do so. Revoking the edge does not necessarily delete the underlying connected account.

**Data flow**: It receives a grant id and actor member id. It checks that the grant belongs to the current agent and that the actor owns or may administer the connection, deletes the grant row if allowed, and returns true if a row was removed.

**Call relations**: User or operator actions that revoke an agent’s access call this. It relies on `_grant_for_actor` to perform the ownership and admin checks before deleting.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 533–590)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Adds the current agent to an already existing connection. The actor must own the connection or the connection must already be shared across the workspace.

**Data flow**: It receives provider, account id, conversation id, actor member id, and a sharing flag. It looks up the existing connection, checks whether attaching is allowed, refuses attempts to newly share a private connection through this path, inserts the grant if missing, and returns whether the connection existed.

**Call relations**: This is used when access should be granted from an already connected account rather than through a fresh OAuth callback. It writes directly to the grant table after permission checks.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 592–620)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the connection behind a grant is shared with the workspace. It protects the owner’s private connection by checking who is making the change.

**Data flow**: It receives a grant id, the desired shared value, and actor member id. It verifies the actor can change the grant’s connection, updates the connection’s shared flag, and returns true if the update happened.

**Call relations**: Sharing controls call this after selecting a grant. It delegates grant lookup and permission checking to `_grant_for_actor`; the admin rules differ depending on whether sharing is being widened or narrowed.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 622–678)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a connection after checking permission. It also detaches and tombstones related feed data so the system stops treating old synced pages as live.

**Data flow**: It receives a connection id and actor member id. It checks whether the actor may mutate that connection, finds sources tied to it, deletes source grants, marks sources removed, tombstones pages from those sources, deletes the connection row, and returns true if the connection was found and processed.

**Call relations**: Disconnect actions call this when a member or allowed admin wants to remove an account. It uses `_connection_for_actor` for the permission gate before performing cleanup.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 680–708)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific connection. It returns the connection id only when the connection exists and the actor has the needed authority.

**Data flow**: It receives an open database connection, connection id, actor member id, and whether admins are allowed. It locks and reads the connection row, returns none if missing, returns the id if the actor owns it, otherwise checks admin status and either returns the id or raises a permission error.

**Call relations**: `GrantStore.disconnect` calls this directly for connection-level changes. `_grant_for_actor` also calls it when a grant operation must first prove the actor may touch the grant’s underlying connection.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 710–720)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Answers whether a member is an admin in the current workspace. It is a small permission helper used during connection checks.

**Data flow**: It receives an open database connection and member id, reads that member’s admin flag for the current workspace, and returns true or false.

**Call relations**: `_connection_for_actor` calls this when the actor is not the connection owner and admin access might be allowed.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 722–760)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a grant belongs to the current agent and whether the acting member may change the connection behind it. It prevents someone from revoking or sharing a grant they should not control.

**Data flow**: It receives an open database connection, grant id, actor member id, and whether admins are allowed. It finds the grant’s connection id, asks `_connection_for_actor` to verify rights, locks and rechecks the grant row, and returns the grant id or none.

**Call relations**: `GrantStore.revoke` and `GrantStore.set_shared` call this before changing grant-related data. It is the shared guardrail for grant mutations.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 781–803)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, turn_id: UUID | None=None) -> str
```

**Purpose**: Creates the provider approval URL for a new connect attempt. It seals all important request details into the OAuth state value so the later callback can be trusted without a separate pending row.

**Data flow**: It receives workspace, agent, provider, member, conversation, sharing, and optional turn information. It finds the provider descriptor, builds a `ConnectState`, encrypts it, asks the provider to create an authorization URL, and returns that URL.

**Call relations**: `ConnectHandoff.authorize` calls this when a turn needs a fresh connect URL. The provider descriptor’s `authorize_url` supplies the final browser link.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 805–810)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a provider name can be connected before a request is accepted. It catches typos or unavailable connectors early.

**Data flow**: It receives a provider name. It returns successfully if the provider is directly registered or if the resolver claims it; otherwise it raises `UnknownProvider`.

**Call relations**: Connect request creation can call this before storing a request. It uses the resolver only for validation, while `knows_provider` is the cheaper later check.

*Call graph*: calls 1 internal fn (__init__).


##### `ConnectFlow.knows_provider`  (lines 812–817)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether this process still has machinery for a provider. It avoids slow external catalog checks when a stored connect request is being opened.

**Data flow**: It receives a provider name and returns true if the provider is in the installed map or if an open resolver exists. It does not call external services.

**Call relations**: `ConnectHandoff.authorize` uses this under a turn-row check before minting or returning a URL. If it returns false, the handoff is treated as no longer valid.


##### `ConnectFlow.bridge_workspace`  (lines 819–825)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to run as. This prevents a bridge request from using mismatched provider or callback details.

**Data flow**: It receives a sealed state, provider name, and callback URL. It opens the state, compares the provider and callback with the expected values, checks the provider exists, and returns the workspace id.

**Call relations**: `connect_bridge_workspace` calls this for incoming bridge requests. If anything does not match, the bridge is rejected.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 827–877)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth callback and makes the connection usable. It verifies the sealed state, exchanges the provider code for an account, records the grant, notifies hooks, and optionally resumes the conversation.

**Data flow**: It receives the sealed state and OAuth code. It opens the state, gets the provider descriptor, enters the correct workspace and agent context, exchanges the code, records the connection through `GrantStore.record`, fires connection hooks if installed, sends a conversation resume message if installed, and returns a `GrantRecorded` summary.

**Call relations**: The OAuth callback handler calls this after the provider redirects back. It coordinates the provider, database store, extension hooks, and conversation resumption in that order so the agent wakes up only after the account is ready.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 879–884)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns a human-friendly name for a provider. If no display label was supplied, it turns the provider slug into title-cased words.

**Data flow**: It receives a provider name, looks for it in the labels map, and otherwise replaces underscores with spaces and title-cases the result. It returns the display string.

**Call relations**: `ConnectFlow.complete` uses this for the message shown to the member and for the returned grant summary.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 886–892)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. It supports both explicitly registered providers and providers produced by an open resolver.

**Data flow**: It receives a provider name, checks the installed provider map, asks the resolver for a descriptor if available, and raises `UnknownProvider` if no descriptor can be found.

**Call relations**: `ConnectFlow.authorize`, `bridge_workspace`, and `complete` all call this before using provider-specific OAuth behavior.

*Call graph*: calls 1 internal fn (__init__); called by 3 (authorize, bridge_workspace, complete).


##### `ConnectFlow._open`  (lines 894–899)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. It rejects state values that were tampered with, unreadable, or too old.

**Data flow**: It receives the state string, asks Fernet encryption to decrypt it within the configured time limit, parses the JSON into a `ConnectState`, and returns that state. If decryption fails, it raises `ConnectStateInvalid`.

**Call relations**: `ConnectFlow.bridge_workspace` and `ConnectFlow.complete` call this before trusting any callback or bridge request details.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 927–1012)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Returns the private connect URL for a specific pending turn and member. It reuses a recent URL when safe, or mints a fresh one when the old one is too close to expiry.

**Data flow**: It receives workspace id, turn id, and member id. It reads and locks the turn, verifies that it still contains a connect request for that member, checks the provider and target agent still exist, returns a memoized URL if still fresh, otherwise asks `ConnectFlow.authorize` for a new URL, stores it on the turn, and returns the winning URL even if another request raced and stored one first.

**Call relations**: Surfaces call this when the member presses or opens the connect control in a conversation. It hands off to `ConnectFlow.authorize` only after rechecking the stored request and uses `_held` to decide whether an existing URL is still safe.

*Call graph*: calls 1 internal fn (_held); 5 external calls (__init__, model_validate, select, update, workspace_tx).


##### `ConnectHandoff._held`  (lines 1014–1025)

```
def _held(self, url: str | None, authorized_at: datetime | None) -> str | None
```

**Purpose**: Decides whether a stored authorization URL is still fresh enough to hand back to the member. This avoids giving out a link whose sealed state might expire before the callback finishes.

**Data flow**: It receives a URL and the time it was authorized. If either is missing, or if the timestamp is older than the memo window, it returns none; otherwise it returns the URL.

**Call relations**: `ConnectHandoff.authorize` calls this before minting a new URL and again after a race to see whether another caller’s newly stored URL can be reused.

*Call graph*: called by 1 (authorize); 3 external calls (now, replace, timedelta).


##### `install_connect_flow`  (lines 1031–1039)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow object. This gives tools, surfaces, and callbacks one shared place to find the configured provider map, encryption key, store, and callback URL.

**Data flow**: It receives a `ConnectFlow` or none and assigns it to the module-level installed-flow variable. It returns nothing.

**Call relations**: Startup code calls this when configuring the server. Tests can also call it to swap in a stub flow.


##### `installed_connect_flow`  (lines 1042–1045)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed connect flow or raises a clear error if connection grants are unavailable. This avoids silent failures when the deployment lacks the needed credential key.

**Data flow**: It reads the module-level installed-flow variable. If present, it returns the flow; if absent, it raises `ConnectUnavailable`.

**Call relations**: `connect_bridge_workspace` calls this before verifying bridge requests. Other connect entry points can also use it as the singleton access point.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 1048–1057)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming connector browser bridge request is valid and, if so, identifies its workspace. It returns none instead of raising for rejected bridge requests.

**Data flow**: It reads `state`, `provider`, and `callback` from the request query string, gets the installed connect flow, and asks it to verify the bridge. It returns the workspace id on success or none if the state, provider, or flow is invalid.

**Call relations**: Browser bridge routing code can call this before running workspace-scoped bridge behavior. It wraps `installed_connect_flow` and `ConnectFlow.bridge_workspace` in a rejection-friendly interface.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 1064–1073)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. The name includes both slugged text and a short hash so two similar-looking accounts do not collide.

**Data flow**: It receives a provider and account id, hashes the combined identity, slugifies both text parts, trims the readable head to the object-name limit, appends the hash qualifier, and returns the final name.

**Call relations**: Portal and object surfaces use this so connection and grant objects are named consistently. It relies on `_slug` for the readable parts.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 1076–1077)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a simple lowercase slug suitable for object names. It keeps letters and digits and replaces other runs of characters with hyphens.

**Data flow**: It receives raw text, lowercases it, replaces non-alphanumeric runs with hyphens, trims edge hyphens, and returns the slug.

**Call relations**: `account_object_name` calls this for both provider names and account ids before adding the collision-resistant hash.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 1080–1088)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-friendly summaries of grants for the current object-target agent. This is the per-agent view used by normal workspace actions.

**Data flow**: It builds a database scope for the current workspace and current agent, passes that scope into `_grant_summaries`, and returns the resulting summary tuple.

**Call relations**: Agent-scoped surfaces call this when they need to show what connector accounts the agent can use. `_grant_summaries` performs the actual database join.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 1091–1094)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-friendly summaries of all connector grants in one workspace. This is useful for operator or admin views that are not limited to one agent.

**Data flow**: It receives a workspace id, enters that workspace context, asks `_grant_summaries` for all grants in that workspace, and returns the result.

**Call relations**: Workspace-wide surfaces call this for an overview. It shares the same row-building helper as the agent-specific `grant_summaries`.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 1097–1148)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Fetches grant summary rows according to a supplied database filter. It joins grants to their connections, agents, and owning members so the result is useful to humans.

**Data flow**: It receives a SQL filter describing the desired scope. It queries the database, orders rows by provider and agent name, converts each row into a `GrantSummary`, and returns the tuple.

**Call relations**: `grant_summaries` and `workspace_grant_summaries` call this with different scopes. It is the shared implementation for grant audit listings.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 4 external calls (__init__, and_, select, workspace_tx).


##### `connection_summaries`  (lines 1151–1228)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the workspace’s connected accounts and the agents currently granted each one. This is a connection-centered view rather than a grant-centered view.

**Data flow**: It reads connections for the current workspace, joins owner member information and any granted agents, groups repeated rows by provider and account id, gathers agent names, and returns `ConnectionSummary` objects.

**Call relations**: Connection management surfaces use this to show accounts, owners, sharing state, and attached agents. It does not depend on the currently bound agent.

*Call graph*: 5 external calls (__init__, and_, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1231–1266)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. Feed registration can use this to know which member-owned accounts the main agent is allowed to sync from.

**Data flow**: It queries connections joined through connector grants to agents marked as the main agent in the current workspace. It orders by provider and account id, converts rows into `MainAgentConnection` objects, and returns them.

**Call relations**: Feed-related code can call this when deciding which connected accounts are available for main-agent syncing. Connections granted only to other shipped agents are intentionally excluded.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### Agent setup readiness
Agent setup checks describe the remaining credentials, account connections, or schedules needed before installed agents can run.

### `core/src/ufo/runtime/kinds/agent_setup.py`

`domain_logic` · `setup screens and member turns`

An installed agent may arrive with the code and prompt it needs, but not with permission to read a user’s accounts or a clock telling it when to run. This file is the shared checklist for that missing setup. Without it, the system could say an agent is ready when it cannot actually access anything, or it could ask users to fix problems they have no button or action for.

The file defines small data shapes for the setup checklist: needed account connectors, stored workspace credentials, standing orders such as scheduled tasks, and cadence choices like “every day at 9.” It also defines the matching “state” objects that say whether each item is already satisfied.

The main read path is `setup_state`. It loads the agent’s declared setup from the database, checks which account grants and credentials exist for the current workspace and member, asks another subsystem whether standing orders are armed, and returns a clear status object for a setup screen.

The turn-time path is `setup_skill`. When a member is talking, it creates a temporary skill telling an agent what accounts are still missing. The main agent can also receive a roster of other installed agents that need setup, like a front desk that can help finish paperwork for the whole office.

#### Function details

##### `SetupCadence._hourly_spans_every_day`  (lines 46–54)

```
def _hourly_spans_every_day(self) -> 'SetupCadence'
```

**Purpose**: This validation step makes sure a schedule choice is meaningful. In particular, it rejects an “every hour” cadence that also names weekdays, because an hourly schedule has no single local hour that can be safely translated across time zones.

**Data flow**: It reads the cadence fields already placed on the object: `hour`, `minute`, and `weekdays`. If the hour is missing, meaning “every hour,” it checks that no weekdays were supplied; it also checks that any weekdays are numbered 0 through 6. If the data makes sense, the same cadence object continues on; if not, validation stops with a clear error.

**Call relations**: This is called automatically by Pydantic, the library used here to check and shape data, whenever a `SetupCadence` is built or validated. It protects later schedule display and conversion code from receiving a cadence that cannot be interpreted honestly.


##### `AgentSetup._a_clock_need_carries_its_offer`  (lines 145–162)

```
def _a_clock_need_carries_its_offer(self) -> 'AgentSetup'
```

**Purpose**: This validation step keeps clock-based setup honest. If an agent says it needs a scheduled task, it must also provide the schedule offer that lets the user arm it; and if it offers schedule choices, it must declare that a scheduled task is actually needed.

**Data flow**: It reads the agent setup declaration, especially the `standing` needs and the optional `schedule` offer. If `scheduled_task` appears without a schedule offer, or a schedule offer appears without a matching scheduled-task need, it raises an error. Otherwise, it returns the same setup object as valid.

**Call relations**: This is run automatically when an `AgentSetup` is validated. It ensures that `setup_state` and setup screens can always connect a visible need to a real action the member can take, instead of showing a dead-end requirement.


##### `_armed`  (lines 249–261)

```
async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder
```

**Purpose**: This helper asks whether the agent already has a standing order of a given kind, such as a scheduled task. For scheduled tasks, it asks for the app’s specific named task so one feature’s schedule is not mistaken for another’s.

**Data flow**: It receives a standing-order kind, the agent’s declared setup, and an async callback named `armed` that knows how to look up real standing orders. If the kind is `scheduled_task` and the setup names a schedule, it passes that schedule name to the callback. Otherwise it asks only whether any order of that kind exists. It returns an `ArmedOrder`, which says whether the order is held and may include the cron schedule string.

**Call relations**: `setup_state` calls this while building the setup status for each declared standing need. `_armed` does not know where extension-specific standing orders are stored; instead, it delegates that lookup to the `armed` callback supplied by the caller.

*Call graph*: called by 1 (setup_state).


##### `setup_state`  (lines 264–361)

```
async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState
```

**Purpose**: This builds the full setup status for one agent as seen by one member. It tells a setup screen which needed accounts, credentials, and standing orders are already in place and which are still missing.

**Data flow**: It receives an agent ID, a member ID, and an `armed` lookup callback. It opens a workspace database transaction, reads the agent’s saved setup declaration, and returns an empty state if there is no declaration. If there is setup to check, it reads account grants that this member can actually use, reads stored credential slots in the workspace, and then leaves the database transaction. Next it marks each declared connector as granted or missing, marks each credential as filled if it exists either in the workspace or in the deployment environment, and calls `_armed` for each standing-order kind. It returns a `SetupState` containing the visible rows for the setup UI.

**Call relations**: This is the main reader for surfaces such as the portal. It calls `_armed` because core knows the declaration but not every extension’s standing-order table. It also creates the state-row objects that the caller can display directly: connector rows, credential rows, standing-order rows, the schedule offer, and the app’s instructions.

*Call graph*: calls 1 internal fn (_armed); 9 external calls (__init__, __init__, __init__, __init__, or_, select, deploy_env, workspace_tx, ws_current).


##### `pending_setup`  (lines 364–420)

```
async def pending_setup(member_id: UUID) -> tuple[tuple[UUID, str, AgentSetup], ...]
```

**Purpose**: This finds installed agents that still need account grants from the current member. It is used when an agent needs to ask, in conversation, for the accounts it cannot yet use.

**Data flow**: It receives a member ID. It reads all non-archived installed agents in the current workspace that have setup declarations. It then reads the connector grants available to that member, counting shared connections and the member’s own private connections. For each shipped agent, it compares declared connector providers with the providers already granted. If any connectors are missing, it returns that agent’s ID, name, and a reduced `AgentSetup` containing just the missing connectors and instructions.

**Call relations**: `setup_skill` calls this at turn time to decide whether to load setup instructions into the agent. It focuses only on connector grants, because those are the setup actions the conversational skill can ask the member to perform with `connect_account`.

*Call graph*: called by 1 (setup_skill); 5 external calls (__init__, or_, select, workspace_tx, ws_current).


##### `_wants`  (lines 423–424)

```
def _wants(missing: AgentSetup) -> str
```

**Purpose**: This turns a list of missing connector providers into a short human-readable phrase. It helps setup instructions say plainly what kind of account the agent needs.

**Data flow**: It receives an `AgentSetup` that contains missing connector names. It formats each connector as “a <provider> account” and joins them with commas. The output is a single phrase used inside setup messages.

**Call relations**: `setup_skill` calls this when composing instructions for either the current agent or the main agent’s roster of other unfinished agents. It is a small wording helper so the message-building code stays readable.

*Call graph*: called by 1 (setup_skill).


##### `setup_skill`  (lines 444–488)

```
async def setup_skill(agent_id: UUID, is_main: bool, speaker_member_id: UUID | None) -> RuntimeSkill | None
```

**Purpose**: This decides whether to load a temporary setup skill into an agent’s turn. The skill tells the model what accounts are missing and instructs it to ask the member to connect them.

**Data flow**: It receives the current agent ID, whether that agent is the main agent, and the speaking member’s ID. If there is no speaking member, it returns nothing because account connection actions require a real member. Otherwise it calls `pending_setup` for that member. If the current agent itself is missing grants, it builds a `RuntimeSkill` telling it what it still needs. If the current agent is the main agent and other agents are unfinished, it builds a roster skill listing those agents and their missing accounts. If nothing actionable is missing, it returns `None`.

**Call relations**: This is used during member-facing turns, not background runs. It calls `pending_setup` to get the live unfinished setup list, uses `_wants` to phrase missing accounts, and returns a `RuntimeSkill` only when the model can actually help the member finish setup.

*Call graph*: calls 2 internal fn (_wants, pending_setup); 1 external calls (__init__).


### Provisioning and onboarding content
Provisioning, trusted onboarding control rules, and demo seeding turn extension declarations and signup decisions into workspace state.

### `core/src/ufo/runtime/kinds/provisioning.py`

`domain_logic` · `workspace setup and extension provisioning`

An extension can ship suggested agents, but a workspace needs ordinary agent records that members can see, use, rename, and customize. This file is the bridge between those two worlds. It reads each active extension manifest, finds its declared agent provisions, and makes sure the workspace has the right live agent rows.

The important rule is ownership. When an extension first creates an agent, the row becomes the workspace’s copy. After that, most fields are left alone so a member’s changes are not lost. Only two extension-owned pieces are refreshed later: the setup information declared by the extension, and the purpose text if the workspace row does not already have one. This is like a starter template: once handed to the user, their copy is theirs, except for a small instruction card that the extension still owns.

The file also prevents name clashes. If an extension wants to create an agent named “helper” but that name is already taken, it tries safe variants such as adding the extension name. It can also “adopt” an existing matching agent instead of making a duplicate. For a main agent provision, it can adopt the workspace’s main agent without replacing that agent’s configuration. The result of each provision is reported as created, adopted, or already present.

#### Function details

##### `AgentProvisioning.apply`  (lines 54–62)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent provisions from all active extension manifests to one workspace. Someone uses this when the system needs to make sure the workspace has the agents that its installed extensions declare.

**Data flow**: It receives a workspace ID and reads the manifests stored inside the AgentProvisioning object. It enters that workspace’s context, walks through every manifest and every agent provision in each manifest, and sends each one to the per-agent provisioning routine. It returns a tuple of outcomes saying, for each declared agent, whether it was created, adopted, or was already present.

**Call relations**: This is the public starting point for the file’s work. It sets the workspace context with ufo.runtime.workspace.ws, then repeatedly calls AgentProvisioning._one to do the detailed database work for each declared agent.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 64–147)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one declared extension agent to one workspace. It decides whether to refresh an existing shipped agent, adopt an existing workspace agent, archive an unsuitable shipped main-agent row, or create a new agent under a safe name.

**Data flow**: It takes a workspace ID, one extension manifest, and one agent provision. Inside a workspace database transaction, it first looks for an existing row that was already provisioned by the same extension under the same declared name. If found, it may refresh the extension-owned fields and report that the agent is present. If not, it looks for a suitable existing workspace agent to adopt. If adoption is not possible, it finds a free name, creates a new row, and reports that it was created. It changes agent rows in the database and returns a ProvisionOutcome describing what happened.

**Call relations**: AgentProvisioning.apply calls this once for every declared agent. During its decision process, it calls AgentProvisioning._fill to update the small extension-owned part of an existing shipped row, AgentProvisioning._identical to decide whether an ordinary existing row is the same as the provision, AgentProvisioning._free_name to avoid name clashes, and AgentProvisioning._create to insert a new agent when needed.

*Call graph*: calls 4 internal fn (_create, _fill, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._fill`  (lines 149–189)

```
async def _fill(self, connection: AsyncConnection, shipped: sa.Row, manifest: Manifest, provision: AgentProvision) -> None
```

**Purpose**: Refreshes only the parts of an already provisioned agent that still belong to the extension. It preserves member-owned edits while still letting newer extension declarations provide updated setup data or a missing purpose.

**Data flow**: It receives an open database connection, the existing agent row, the manifest, and the provision. It compares the stored setup with the setup declared now, and it checks whether the stored purpose is missing. If nothing needs changing, it does nothing. If something needs changing, it updates those fields, records the manifest version that supplied them, and updates the timestamp.

**Call relations**: AgentProvisioning._one calls this after it finds a shipped agent that already belongs to the workspace. This function performs the careful, limited update instead of letting _one overwrite the whole agent row.

*Call graph*: called by 1 (_one); 2 external calls (execute, update).


##### `AgentProvisioning._free_name`  (lines 191–216)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds a usable agent name when the extension’s declared name may already be taken. It protects both member-created agents and agents from other extensions from being overwritten.

**Data flow**: It receives a database connection, workspace ID, extension name, and declared agent name. It reads all active agent names in the workspace, then tries the declared name first, followed by versions that include the extension name and then a number. It returns the first unused name. If it cannot find one within the built-in limit, it raises an error.

**Call relations**: AgentProvisioning._one calls this only when it has decided a new agent must be created. The chosen name is then passed to AgentProvisioning._create so the new row can be inserted without colliding with existing agents.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 218–275)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Creates a new ordinary workspace agent from an extension’s provision. It records where the agent came from, but it does not give the new agent extra permissions, credentials, or connections on behalf of the extension.

**Data flow**: It receives an open database connection, workspace ID, manifest, provision, and the final chosen name. It takes the provision’s specification, chooses an icon if one was not declared, generates a new unique ID, and inserts a new agent row with the provisioned name, version, setup, prompt, model, visibility, tools, and other starting settings. The database is allowed to ignore the insert if another process already created the same provisioned row first.

**Call relations**: AgentProvisioning._one calls this after name selection when no existing row can be refreshed or adopted. This function may call auto_agent_icon to pick a friendly unused icon, and it uses a conflict-safe insert so simultaneous provisioning attempts do not fail a user’s turn.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 277–290)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing workspace agent already matches what an extension provision would create. This lets the system adopt that row instead of creating a duplicate under another name.

**Data flow**: It receives an existing database row and an agent provision. It compares the row’s prompt, model, reasoning setting, internet access setting, sandbox size, visibility, and tools with the provision’s specification. It returns true if those starting settings match, and false otherwise.

**Call relations**: AgentProvisioning._one uses this while considering adoption of an ordinary, unprovisioned workspace agent. If the row is identical, _one marks it as provisioned by the extension instead of calling AgentProvisioning._free_name and AgentProvisioning._create.

*Call graph*: called by 1 (_one).


### `core/src/ufo/onboard/onboard_control.py`

`domain_logic` · `request handling`

This file is the “front desk” for hosted onboarding, but it is not public. It exposes a small set of internal web routes under `/internal/onboard/`, and every request must carry a special bearer token. That token is like a staff badge: without it, the caller cannot even ask the onboarding service to look at the database.

The main job is to turn a verified email signup into the right workspace membership. It checks that the email, domain, and signup subject agree with one another. Then it can create the workspace if it is new, add the person as a member, create the default main agent, and give a first-time signup credit and spending reserve. If the workspace already exists, it joins the person without accidentally granting new money or changing who owns the workspace.

The file also supports related signin needs: checking whether an existing member is still an admin, listing workspace choices for a verified address, counting all workspaces for a fleet display, and paging through pending invitations.

One especially careful part is how intake-form text reaches the agent prompt. The form is untrusted, so the text is wrapped behind a “wall” that marks it as outside information, and dangerous prompt variable braces are weakened. Without that, someone could submit text that breaks future prompts or smuggles instructions into a powerful system prompt.

#### Function details

##### `_inert`  (lines 172–186)

```
def _inert(answer: str) -> str
```

**Purpose**: This function makes a form answer safe to place inside an agent prompt by defusing doubled curly braces like `{{` and `}}`. Those braces have special meaning in prompt templates, so leaving them untouched could make every future turn in a workspace fail.

**Data flow**: It receives one text answer from the intake form. It repeatedly replaces doubled opening and closing braces with single braces until no doubled brace remains. It returns text that still looks close to what the person typed, but no longer contains live prompt-template markers.

**Call relations**: It is used by `agent_prompt` when signup intake text is being added to a new workspace’s main agent prompt. Its job is to clean the raw answers before they are wrapped as untrusted outside information.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 189–208)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: This function builds the initial prompt for the default agent in a newly created workspace. If there is no intake profile, it uses the normal default prompt; if there is one, it adds the intake answers as clearly marked, untrusted background information.

**Data flow**: It receives either no profile or a profile containing business and goals text. With no profile, it returns the standard default agent prompt unchanged. With a profile, it first passes each answer through `_inert`, then wraps the combined text with `wall`, which labels it as coming from the intake form, and finally returns the default prompt plus that protected context.

**Call relations**: `OnboardControl._seat` calls this while creating the workspace’s main agent. This function hands `_seat` the exact prompt text to store in the agent table, and it delegates the safety work to `_inert` and the shared untrusted-text `wall` helper.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `_labelled`  (lines 211–247)

```
def _labelled(rows: Sequence[sa.RowMapping], subject: str) -> list[WorkspaceChoice]
```

**Purpose**: This function turns raw database rows about possible workspaces into a clear list a signing-in person can choose from. It also prevents an ambiguous signup subject from silently matching more than one workspace.

**Data flow**: It receives database rows and the verified signup subject, such as a domain or exact email. It computes each workspace’s human label from its first member, filters subject matches so only the correct workspace remains, marks which choices are already direct memberships, and adds a short UUID prefix when two labels would otherwise look the same. It returns a list of `WorkspaceChoice` objects, or raises an HTTP error if the subject points to multiple workspaces.

**Call relations**: `OnboardControl._choices` fetches candidate rows across workspaces and then calls `_labelled` to make them safe and understandable for the caller. `_labelled` relies on `workspace_subject` to derive the same label used elsewhere in onboarding.

*Call graph*: called by 1 (_choices); 3 external calls (__init__, HTTPException, workspace_subject).


##### `_verified_signup`  (lines 250–269)

```
def _verified_signup(email: str, domain: str | None, signup_subject: str | None) -> tuple[str, str, str]
```

**Purpose**: This function checks that the signup identity really matches the verified email address. It protects against a caller claiming a different domain or signup subject than the email proves.

**Data flow**: It receives an email, an optional domain, and an optional signup subject. It trims and lowercases them, derives the real domain from the email, rejects mismatches with HTTP validation errors, and decides the final signup subject when it was not explicitly supplied. It returns three normalized strings: the member email, the verified domain, and the accepted signup subject.

**Call relations**: Both `OnboardControl._seat` and `OnboardControl._choices` call this before they touch workspace membership decisions. It is the shared gate that makes sure the rest of the onboarding flow is based on a proven email identity rather than caller-supplied guesswork.

*Call graph*: called by 2 (_choices, _seat); 2 external calls (HTTPException, email_domain).


##### `OnboardControl.router`  (lines 279–286)

```
def router(self) -> APIRouter
```

**Purpose**: This method builds the internal FastAPI router for onboarding. It registers the five private routes and attaches the token check that protects all of them.

**Data flow**: It starts with the `OnboardControl` instance, including its configured control token. It creates a router with the `/internal/onboard` prefix, attaches `_guard` as a dependency that runs before route work, and registers routes for seating, membership, choices, fleet count, and invitations. It returns the ready-to-mount router.

**Call relations**: Application setup code calls this when it wants to expose the internal onboarding API. The router then calls `_guard` before handing individual requests to `_seat`, `_membership`, `_choices`, `_fleet`, or `_invitations`.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 288–290)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This method refuses any request that does not carry the exact onboarding control token. It is the security door for all routes in this file.

**Data flow**: It receives the HTTP `Authorization` header, defaulting to an empty string if absent. It compares it with `Bearer <control_token>` from the `OnboardControl` instance. If it matches, nothing is returned and the request continues; if not, it raises a 401 HTTP error.

**Call relations**: `OnboardControl.router` attaches this guard to every internal onboarding route. FastAPI runs it before route methods such as `_seat` or `_choices`, so unauthorized callers are stopped before any database read or write.

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 292–380)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

**Purpose**: This method creates or joins the workspace for a verified signup and ensures the person has a member seat there. For a brand-new workspace, it also creates the default agent and grants the initial signup balance.

**Data flow**: It receives a `SeatRequest` containing a workspace ID, email identity fields, and optional intake profile. It verifies the signup identity, enters the workspace-scoped database context, tries to insert the workspace, locks the workspace row to avoid races, checks that the workspace belongs to the signup subject, creates or finds the member, creates the default main agent with `agent_prompt`, and grants initial credit only when the workspace was truly founded here. It returns an `EnsuredWorkspace` showing the workspace ID, whether the member is an admin, and whether this signin founded the workspace.

**Call relations**: This is the main endpoint behind `POST /internal/onboard/seat`. It depends on `_verified_signup` for identity safety, `agent_prompt` for safe default-agent context, `create_member` for seat rules, and billing helpers for the startup credit and reserve. It is called only after `_guard` has accepted the internal control token.

*Call graph*: calls 2 internal fn (_verified_signup, agent_prompt); 12 external calls (__init__, HTTPException, insert, select, credit, set_reserve, workspace_tx, create_member, signup_workspace_id, workspace_subject (+2 more)).


##### `OnboardControl._membership`  (lines 382–402)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

**Purpose**: This method checks whether a given email is still a member of a specific workspace, and whether that member is an admin. It prevents a removed member from being quietly recreated during signin.

**Data flow**: It receives a workspace ID and email. It normalizes the email, opens a workspace-scoped database transaction, and looks for that member’s admin flag. If no member row exists, it raises a 404 error; otherwise it returns a `Membership` object containing the admin status.

**Call relations**: This backs the membership-check route registered by `OnboardControl.router`. It is used after a workspace choice has been made, so a revocation that happened between listing choices and selecting one is respected rather than undone.

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 404–429)

```
async def _choices(self, email: str, domain: str | None=None, signup_subject: str | None=None) -> WorkspaceChoices
```

**Purpose**: This method lists the workspaces a verified email address may enter. It includes direct memberships and, when valid, the workspace named by the person’s verified signup subject.

**Data flow**: It receives an email plus optional domain and signup subject. It normalizes and verifies them with `_verified_signup`, logs a warning because this is a cross-workspace read, then uses the owner-level database path to run a query that can see across tenants. It passes the result rows to `_labelled` and returns a `WorkspaceChoices` object containing clear, disambiguated choices.

**Call relations**: This backs the choices route registered by `OnboardControl.router`. It uses the broader `owner_tx` database path because workspace choices cannot be found from inside one workspace. After the database query, it hands the raw candidates to `_labelled` to enforce subject matching and produce user-facing labels.

*Call graph*: calls 2 internal fn (_labelled, _verified_signup); 5 external calls (__init__, text, owner_tx, warn, signup_workspace_id).


##### `OnboardControl._fleet`  (lines 431–438)

```
async def _fleet(self) -> Fleet
```

**Purpose**: This method counts how many workspaces exist. It supplies the landing page’s live fleet number.

**Data flow**: It takes no route-specific input. It logs a warning because it reads across all workspaces, opens an owner-level database transaction, counts rows in the workspace table, and returns that number inside a `Fleet` object.

**Call relations**: This backs the fleet route registered by `OnboardControl.router`. Like `_choices`, it uses `owner_tx` because the answer is global rather than tied to one workspace, and it is protected by `_guard` before it runs.

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


##### `OnboardControl._invitations`  (lines 440–513)

```
async def _invitations(self, after_invited_at: datetime | None=None, after_workspace_id: UUID | None=None, after_email: str | None=None) -> Invitations
```

**Purpose**: This method returns one page of pending teammate invitations, oldest first. It lets a polling caller send or process invitation emails without loading the entire invitation table at once.

**Data flow**: It receives an optional cursor made of three parts: invitation time, workspace ID, and email. If only part of the cursor is supplied, it rejects the request because partial cursors can skip or duplicate rows. It builds a database query for invited members, joins each invite to the inviter and the workspace’s first member, applies the cursor when present, limits the result to one page, and returns `Invitation` objects with workspace labels, invitee emails, inviter emails, and invite times.

**Call relations**: This backs the invitations route registered by `OnboardControl.router`. It uses the owner-level database path because invitations are gathered across all workspaces. Unlike `_choices` and `_fleet`, it intentionally avoids warning logs on every call because a repeating sweep would create noisy logs.

*Call graph*: 9 external calls (__init__, __init__, HTTPException, DateTime, literal, select, tuple_, owner_tx, workspace_subject).


### `core/src/ufo/onboard/seed.py`

`domain_logic` · `onboarding or manual demo seeding`

This file is like a stage crew setting up a fully dressed sample scene so designers, developers, or operators can check whether the conversation UI still looks right. Instead of waiting for a real user and agent run to naturally produce every possible shape of data, it writes a finished conversation directly into the durable stores: the database rows for conversations and turns, plus transcript and file blobs.

The important safety rule is that it only deletes earlier demo runs that it can prove were created by this seed process. It marks those rows with a private queue key prefix and checks that no real member speech or transcript disclosure has become part of the run. If a real user touched it, it is treated as workspace history and left alone.

The main `KitchenSink` object first clears old safe-to-remove demo data, then opens a new web conversation, adds three completed turns, creates nested subagent conversations, attaches two shared files, and writes the visible transcript. The transcript contains realistic examples: user prompts, assistant replies, tool calls, tool results, markdown tables, code blocks, and a final pending question. Without this file, the project would lack a repeatable way to populate the portal with one compact conversation that exercises many display paths at once.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This small helper creates a user message that includes a hidden-looking context header pointing back to the turn it belongs to. It lets the sample transcript mimic the way real messages can carry a reference to engine state.

**Data flow**: It receives a turn ID and the words the user said. It wraps the turn ID inside a context block, appends the user text, and returns a `Message` object marked as coming from the user.

**Call relations**: `KitchenSink._said` calls this when building the visible transcript. It gives the transcript’s user messages enough structure to look like real framed conversation entries.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the main action for creating the kitchen-sink demo conversation. Someone calls it when they want a fresh sample conversation written into the workspace.

**Data flow**: It starts by making a new conversation ID and three new turn IDs. It clears old safe demo runs, opens the new conversation and turns, creates subagent runs, attaches files, writes the final transcript blob, and returns the new conversation ID.

**Call relations**: This method is the top of the flow in this file. It calls the cleanup, database-writing, subagent, file, and transcript-building helpers in order, so the demo appears as one complete conversation in the portal.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes earlier kitchen-sink demo runs that are safe to delete. It prevents the workspace from filling up with old demo copies every time the seed is run.

**Data flow**: It asks `_prior` for earlier demo runs that were created by this seed and not touched by real user history. For each one, it deletes the database rows through `_drop`, removes the web chat row from the extension store, and deletes related blobs such as transcripts and shared artifacts.

**Call relations**: `KitchenSink.write` calls this before creating a new demo. It relies on `_prior` to identify safe targets and `_drop` to remove database records, then finishes the cleanup in the blob store and extension store.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This finds old kitchen-sink runs that the seed is allowed to erase. Its job is to be careful: only data that looks entirely seed-created is returned.

**Data flow**: It reads the workspace database for web conversations whose queue key starts with the seed prefix. For each root conversation, it follows linked turns and subagent conversations, then checks whether any turn lacks the seed marker or any transcript access record exists. If nothing suggests real user history or disclosure, it returns that run as a `_PriorRun` record.

**Call relations**: `KitchenSink._clear` calls this at the start of cleanup. It hands back only vetted runs, so the later deletion step does not accidentally remove a member’s real conversation or audited transcript access.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database side of one old safe demo run. It removes the conversation tree, its turns, conversation change records, and shared artifact records.

**Data flow**: It receives a `_PriorRun` that lists conversations and turns. It first collects blob keys for shared artifacts so those files can be deleted later, then deletes artifact rows, conversation change rows, turn rows, and conversation rows from the database. It returns the artifact blob keys it found.

**Call relations**: `KitchenSink._clear` calls this after `_prior` has decided a run is safe to remove. `_drop` removes database records and hands artifact blob keys back to `_clear`, which deletes the actual stored blobs afterward.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three completed turns. It gives the portal the database records it needs to show the demo as a normal chat.

**Data flow**: It receives a conversation ID and three turn IDs. It inserts a conversation row with a seed-marked queue key, inserts three done turns using terminal frames from `_terminals`, retitles the conversation to “Kitchen sink,” and writes a matching chat row into the web extension store.

**Call relations**: `KitchenSink.write` calls this after cleanup. It calls `_terminals` to get the finished-turn summaries, writes the core database rows, then hands off to the surface and extension-store helpers so the web portal can find and label the chat.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final status summaries for the three demo turns. These summaries include model name, token count, cost, and, for the last turn, a still-open question for the user.

**Data flow**: It takes no outside input beyond the `KitchenSink` instance. It creates three `TerminalFrame` objects: two simple completed frames and one completed frame that also contains structured questions with options and a free-text prompt. It returns them as a tuple.

**Call relations**: `KitchenSink._open` calls this while inserting turn rows. The returned frames become the stored terminal state for each demo turn, giving the UI cost lines and question widgets to render.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–371)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This creates the demo’s nested subagent activity. It shows that a main conversation can spawn a subagent, and that subagent can spawn another subagent.

**Data flow**: It receives the main conversation ID and the parent turn ID that should appear to have spawned the first subagent. It creates two linked subagent runs with `_run`, then writes a transcript blob for the first spawned conversation containing a user message, an assistant tool call, and a tool result.

**Call relations**: `KitchenSink.write` calls this after opening the main conversation. It calls `_run` twice to create the database records, then writes transcript data so the subagent surface has realistic content to display.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 373–408)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This creates one completed subagent conversation and its single completed turn. It is the building block used to make the nested subagent part of the demo.

**Data flow**: It receives a turn ID, the parent turn ID, a subagent profile name, and the short result text that subagent should return. It creates a new conversation whose queue key points to the parent, inserts one done turn with the chosen profile and result, and returns the new subagent conversation ID.

**Call relations**: `KitchenSink._runs` calls this for both the child and grandchild subagent examples. Each call writes one conversation-and-turn pair that the portal can connect back to the parent turn.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 410–431)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This attaches two sample files to one of the demo turns: a markdown audit and a CSV metrics file. These give the portal real shared artifacts to list and open.

**Data flow**: It receives the turn ID that should own the files. For each built-in file body, it encodes the text, creates a unique blob key, stores the bytes in the blob store, and inserts a shared artifact row with filename, media type, size, and workspace details.

**Call relations**: `KitchenSink.write` calls this after creating the conversation and subagent runs. It writes both the file contents and their database records so the conversation can show attachments tied to the chosen turn.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 433–499)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the visible main transcript for the demo conversation. It is where the sample chat content is written: user requests, assistant replies, tool calls, tool results, markdown, and code.

**Data flow**: It receives the three turn IDs. It uses `_framed` to create user messages tied to those turns, creates assistant and tool-result messages around them, and returns the full ordered message tuple.

**Call relations**: `KitchenSink.write` calls this just before encoding and storing the transcript blob. It supplies the human-readable conversation body that matches the turns and artifacts created elsewhere in the file.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### Extension connection flows
Extension-specific setup connects workspaces or members to GitHub and iMessage while validating ownership and permissions.

### `extensions/coding/ufo_ext_coding/connect.py`

`domain_logic` · `GitHub connection setup and browser callback`

This file protects the “connect GitHub” setup flow. A workspace admin first asks ufo for an install link. That link goes to GitHub and includes a sealed piece of state, which is like a tamper-resistant note saying which workspace and credential slot this setup belongs to. After the admin installs the GitHub App, GitHub sends the browser back to ufo with an authorization code and an installation ID.

The important safety point is that the installation ID is not trusted by itself. An ID is just a number, and a bad or mistaken user could try to swap in someone else’s number. So this file asks GitHub directly: it exchanges the returned authorization code for a temporary token for the actual GitHub member who authorized, then calls GitHub’s “user installations” API to see which ufo App installations that member can reach. Only if the claimed installation appears in that GitHub-provided list does ufo bind it to the workspace.

Once the connection succeeds, the workspace credential slot stores the verified installation. The callback page then sends the user back to ufo’s home page if there is one, or tells them they can close the tab. Without this file, workspace-wide GitHub access could be connected insecurely or not connected at all.

#### Function details

##### `connect_github`  (lines 54–78)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: This is the tool action an admin uses to start connecting GitHub. It checks that the speaker is a workspace admin and that this ufo deployment has a GitHub App configured, then returns a GitHub install link with protected state attached.

**Data flow**: It receives the current tool context and an empty input object. It reads whether the speaker is an admin, checks the configured GitHub App ID, asks the credential system to create a sealed authorization state for the GitHub installation slot, and returns a tool result containing a human-readable install link. It does not save the installation yet; it only starts the setup trip to GitHub.

**Call relations**: This begins the connection story. It calls the tool context to confirm admin rights and create the sealed state, asks the manifest for the GitHub App ID, then wraps the message in TextContent and ToolResult so ufo can show it to the admin. GitHub later sends the user back to the callback handled by github_installed.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 81–86)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: This reads the protected state GitHub returned and figures out which workspace the callback belongs to. It is a small helper for turning a browser redirect, which has no chat or thread attached, back into a workspace-aware request.

**Data flow**: It receives an HTTP request and looks for the state value in the query string. It passes that state, along with the expected credential slot and payload name, to the credential authorization checker. If the state is valid, it returns the workspace ID; if not, it returns nothing.

**Call relations**: This function relies on authorized_slot_workspace to verify the sealed state rather than trusting raw request data. It fits into the callback routing path: before a GitHub return can be associated with a workspace, the state created by connect_github must be opened and checked.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 105–132)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: This asks GitHub whether the GitHub user who just authorized can actually see the installation ID being claimed. It is the key anti-spoofing check in the connection flow.

**Data flow**: It receives GitHub’s authorization code and the installation ID from the callback URL. It sends the code, client ID, and client secret to GitHub to get an access token for that GitHub user. If GitHub does not return a token, it raises an authorization error. With the token, it asks GitHub for the user’s visible App installations, filters that list to this ufo App, and returns true only if the claimed installation ID is present.

**Call relations**: github_installed uses this method after GitHub redirects back to ufo. Internally it talks to GitHub through an HTTP client. If GitHub refuses the code, it raises GitHubAuthorizationError so the callback can show a clear failure page instead of saving anything.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 135–146)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: This builds the object that knows how to verify a GitHub installation using this deployment’s GitHub App identity. It gathers the App ID, client ID, and client secret needed for the return-leg security check.

**Data flow**: It reads the GitHub App ID from the coding extension manifest and reads the client ID and client secret from environment variables. If no GitHub App is configured, it stops with an error. Otherwise it returns a GitHubInstallExchange ready to contact GitHub.

**Call relations**: github_installed calls this when it needs to verify the installation returned by GitHub. It creates GitHubInstallExchange with the deployment’s credentials, keeping the callback code separate from the details of where those credentials come from.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 149–184)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This handles the browser page GitHub redirects to after someone installs and authorizes the ufo GitHub App. It validates the callback, verifies the installation with GitHub, saves the connection if it is legitimate, and shows the user a final page.

**Data flow**: It receives the extension context and HTTP request. It reads the authorization code and installation ID from the query string. If either is missing, it returns an error page. Otherwise it builds an install exchange and asks whether the authorizing GitHub user reaches that installation. If GitHub rejects the authorization or the installation does not match that user, it returns a failure page. If the check succeeds, it binds the installation ID into the workspace credential slot, looks up the ufo home URL, and returns a success page with either a return link or instructions to close the tab.

**Call relations**: This is the return half of the flow started by connect_github. It calls install_exchange to create the GitHub verifier, then uses callback_page to present clear browser responses. On success, it uses the extension context’s credentials area to save the verified installation and home_url to decide where to send the user next.

*Call graph*: calls 2 internal fn (home_url, install_exchange); 2 external calls (__init__, callback_page).


### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file solves a practical safety problem: the system must not start texting a phone number until the real owner of that phone has proved they control it. Think of it like a coat-check ticket for a phone number: the workspace can reserve the number, but the member still has to show the matching ticket by sending a special code from that phone.

The main tool accepts a phone number, cleans it into a standard US format, and then walks through the connection process. First it checks that the request comes from a signed-in member. Then it checks that this deployment has iMessage provider credentials. If the workspace has not yet connected this iMessage provider, only an admin is allowed to bind it.

Next it tries to reserve the requested phone number for the member. If the number already belongs to someone else, the tool stops. If the number is already linked to this member, it simply assigns a messaging line and says the phone is connected. Otherwise, it creates or reuses a pending claim with a temporary opt-in code. It shares a QR code image so a desktop user can scan it with their phone, and it returns an SMS/iMessage link and text instructions. The claim expires after 30 minutes, so unfinished connections do not stay open forever.

#### Function details

##### `opt_in_link`  (lines 37–41)

```
def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str
```

**Purpose**: Builds a phone link that opens Apple Messages with the opt-in text already filled in. This reduces mistakes because the member does not have to manually type the code.

**Data flow**: It receives the assigned messaging phone number and the temporary opt-in code. It combines them with the fixed opt-in phrase, safely encodes the message text for use inside a link, and returns an `sms:` link that a phone can open.

**Call relations**: This helper is used when preparing the pending-connection response. `_opt_in_result` calls it so the final tool output can include a tappable way for the member to send the required first message.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `opt_in_qr`  (lines 44–52)

```
def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes
```

**Purpose**: Creates a QR code image for the same opt-in message. This is useful when the member is reading instructions on a computer and needs an easy way to continue on their phone.

**Data flow**: It receives the assigned messaging number and opt-in code. It builds an SMS-style QR payload, renders it as a PNG image in memory, and returns the raw image bytes.

**Call relations**: The main connection flow, `ImessageConnect.run`, calls this after a pending claim exists. It then hands the image to the tool context as a shared artifact so the user can scan it.

*Call graph*: called by 1 (run); 2 external calls (BytesIO, make).


##### `_display_phone`  (lines 58–62)

```
def _display_phone(phone_number: str) -> str
```

**Purpose**: Turns a standard US phone number into a friendlier form for human-readable instructions. If the number is not in the expected US format, it leaves it unchanged.

**Data flow**: It receives a phone number string. If it matches the expected `+1...` pattern, it formats it like `(415) 555-0123`; otherwise it returns the original string.

**Call relations**: Both the main connection flow and `_opt_in_result` use this when writing messages for the member. It keeps user-facing instructions readable without changing the stored phone number.

*Call graph*: called by 2 (run, _opt_in_result).


##### `ImessageConnectInput._e164`  (lines 73–85)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Validates and normalizes the phone number supplied to the iMessage connection tool. It accepts common US phone-number formatting, but rejects letters and invalid numbers.

**Data flow**: It receives the raw phone number from the tool input. It trims spaces, removes punctuation and other non-digit formatting, checks that the result is a valid 10-digit US number, and returns it in E.164 form, such as `+14155550123`. If the input is not acceptable, it raises a validation error.

**Call relations**: This is run automatically by the input model before `ImessageConnect.run` receives its arguments. That means the rest of the file can work with one consistent phone-number format instead of guessing what the user typed.


##### `_result`  (lines 88–94)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Builds the standard response shape returned by this tool. It packages a connection state, a human instruction, and any extra details into a tool result.

**Data flow**: It receives a state string, an instruction string, and optional extra fields. It turns those values into JSON text, wraps that text as tool content, marks it as untrusted user-facing output, and returns the completed tool result.

**Call relations**: This is the common exit door for the tool. `ImessageConnect.run` uses it for success, failure, and permission messages, while `_opt_in_result` uses it to produce the special pending opt-in response.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 97–106)

```
def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult
```

**Purpose**: Creates the response shown when the phone connection is waiting for the member to send the opt-in message. It tells the member exactly what to text, where to text it, and includes a link to make that easier.

**Data flow**: It receives the assigned messaging number and the temporary opt-in code. It builds the full opt-in text, formats the phone number for display, creates the tappable SMS link, and returns a pending-state tool result with all of those details.

**Call relations**: The main connection flow calls this after it has created or found a pending claim and shared the QR code. It uses `_display_phone`, `opt_in_link`, and `_result` to turn the claim data into user-facing instructions.

*Call graph*: calls 3 internal fn (_display_phone, _result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 113–168)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full iMessage phone-connection process for one member. It checks who is asking, connects the workspace to the provider if allowed, reserves the phone number, and either completes or starts the opt-in process.

**Data flow**: It receives the tool context, which includes the signed-in member, workspace extension services, storage, and artifact-sharing ability, plus a validated phone number. It checks for a signed-in member, loads the iMessage provider, confirms or binds the workspace installation, reserves the phone claim, assigns a messaging line when appropriate, stores a pending claim with a secret opt-in code if needed, shares a QR code image, and returns a structured result telling the member what happened next.

**Call relations**: This is the central function of the file and the function the tool system calls to perform the action. Along the way it calls the small helpers in this file to format phone numbers, create QR codes, and build consistent results; it also calls the broader platform context to check admin status, bind installations, reserve addresses, store pending claims, and share the QR artifact.

*Call graph*: calls 6 internal fn (share_artifact, speaker_is_admin, _display_phone, _opt_in_result, _result, opt_in_qr); 6 external calls (__init__, now, choice, claim_key, read_claim, uuid4).

## 📊 State Registers Touched

- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-agent-setup-state` — Durable setup checklist and progress state for provisioned agents, including required account links, credentials, schedules, and extension-specific onboarding needs.
- `reg-membership-access-policy` — Durable workspace membership, owner/admin role, seat, invitation, and mutation-permission state used to decide what a member may manage beyond simple object visibility.

# External Connector, OAuth, and Provider Action Flows  `stage-13`

This stage is the system’s “safe plug adapter” for outside services. It is used when a workspace or member connects an app, and later when an agent uses that app during normal work. The main connector and grant files decide who owns a connection, which agent may use it, and whether a secret is held by UFO or by a broker service. The callback file finishes browser approval flows, such as OAuth, where a user approves access on another site and returns to UFO.

Composio and Pipedream files provide hosted bridges to many apps. Their clients create consent links, check connected accounts, list tools or actions, run them, move files, and proxy web requests so raw tokens never enter the sandbox. Composio also has live tool discovery through its MCP tool router.

GitHub App files connect a workspace to GitHub and turn a verified installation into a short-lived access token. Shared connector tools let agents search and run available tools safely. Slack tools guide setup and lookup conversations. The YC bridge connects credentials and allows read-only YC actions.

## Files in this stage

### Connector foundations
Shared connector and grant primitives define how accounts are linked, authorized, and completed through OAuth callbacks without exposing secrets.

### `core/src/ufo/connectors.py`

`domain_logic` · `cross-cutting`

This file is the rulebook and routing desk for connectors. A connector is the system’s bridge to an outside provider. The hard problem is authentication: the system must let sync jobs and tools call providers, but it must not leak API keys or account tokens into logs, sandboxes, or agent-visible data.

The file models three safe ways to authenticate. A `Credential` may contain a special HTTP transport that sends requests through a broker, a bearer token used directly by the host process, or custom headers for providers that need another style of authentication. Its printed form is deliberately redacted, like a password field that only says “hidden.”

It also defines the broker interface. A broker can list tools, describe a tool’s input shape, execute a tool for a connected account, prepare file uploads, expose file outputs, and provide credentials for feed sync. Files are passed as references such as URLs or workspace paths, not as raw bytes through this process.

The registry is the main map from provider name to broker. It can also ask an open resolver about providers that were not registered one by one. For feed-sync sources, `SourceCredentialResolver` binds credential lookup to a specific member-owned connection. Before and during use, the code checks the database to make sure that connection still belongs to the workspace, owner, provider, and account. If it no longer does, the source is stopped instead of silently using stale access.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text form of a credential for debugging without showing the actual secret. This protects tokens and headers from being exposed in logs or error messages.

**Data flow**: It reads which authentication path is present on the credential: proxy transport, bearer token, headers, or none. It then returns a short label that says what kind of credential exists, with the sensitive value replaced by “redacted.” Nothing else is changed.

**Call relations**: This is used automatically by Python when a `Credential` is printed or appears in an error or debug view. It supports the whole connector flow by making accidental exposure less likely whenever credential objects pass through the system.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Describes the method any authentication backend must provide to turn a workspace, provider, and account into a usable `Credential`. Implementations may fetch a direct key or return a brokered transport that keeps the secret elsewhere.

**Data flow**: The caller supplies a workspace ID, provider name, and account handle. The implementation looks up or prepares the right authentication method and returns a `Credential` object. The protocol itself does not perform the lookup; it defines the required shape.

**Call relations**: Feed-sync code and resolver classes call through this interface so they do not need to know which backend is in use. Broker-backed and direct-key backends can both fit behind the same call.


##### `stale_grant_guidance`  (lines 93–100)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error message for the case where a broker no longer recognizes an account grant. It tells the user-facing system that retrying is not enough and the member should reconnect the account.

**Data flow**: It receives a provider name and inserts it into a fixed explanatory sentence. The output is plain text guidance that can be attached to broker errors.

**Call relations**: Broker implementations can use this helper when an old or moved connection points at an account the current broker cannot use. It keeps the recovery message consistent across providers.


##### `ConnectorBroker.tools`  (lines 170–172)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Describes the broker operation for finding tools offered by one provider. A tool here means an action the agent can ask the provider to perform, such as searching mail or creating an issue.

**Data flow**: The caller provides the workspace, provider, and a search query. The broker implementation searches its catalog and returns matching `BrokerTool` descriptions. This protocol method only states the contract.

**Call relations**: Dynamic connector discovery calls this through the broker chosen by the registry. The returned tools can later be described in detail or executed.


##### `ConnectorBroker.schema`  (lines 174–174)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Describes the broker operation for getting the full input shape for one specific tool. This lets the agent know what arguments it may send.

**Data flow**: The caller gives the workspace, provider, and tool slug. The broker returns a `BrokerTool` with its input schema filled in, or reports that the slug is unknown. The protocol defines the expected behavior.

**Call relations**: After a tool is found, describe-style flows call this before execution. If the broker cannot find the tool, callers can treat it as unresolved instead of guessing.


##### `ConnectorBroker.execute`  (lines 176–184)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Describes the broker operation for running a provider tool for a connected account. The important safety point is that the broker injects the account token itself, so the token does not pass through the agent or sandbox.

**Data flow**: The caller sends workspace and provider details, the tool slug, arguments, the account ID to use, and an optional idempotency key, which helps avoid duplicate effects on retries. The broker performs the provider action and returns a dictionary response.

**Call relations**: Dynamic connector tools call this after choosing a provider, account, and tool. File staging and schema lookup may happen before it; file output extraction may happen after it.


##### `ConnectorBroker.file_outputs`  (lines 186–186)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Describes how a broker reports files produced by a tool execution. It turns a raw execution response into named downloadable file references.

**Data flow**: The broker receives the execution response dictionary. It extracts any produced files and returns `BrokerFile` objects containing a filename and short-lived URL. The protocol does not define the extraction details.

**Call relations**: After `execute` returns, connector tooling can ask the broker to identify file outputs so the sandbox can fetch them directly. This keeps file bytes out of the serve process.


##### `ConnectorBroker.stage_upload`  (lines 188–196)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Describes the broker operation for preparing a workspace file so a provider tool can use it as input. Instead of moving file bytes through this process, it returns a place where the sandbox can upload them directly.

**Data flow**: The caller supplies the workspace, provider, tool slug, filename, MIME type, and MD5 checksum. The broker returns a `StagedUpload` with a PUT URL when bytes need uploading, plus the argument value to pass into the tool. If the broker already has the bytes, the PUT URL can be absent.

**Call relations**: Connector tool execution uses this before `execute` when an argument points at a workspace file. The sandbox then uploads to the broker store itself, and the tool call receives only the staged reference.


##### `ConnectorBroker.search`  (lines 198–198)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Describes a richer broker search that can return matching tools plus advice, plans, or warnings. It helps an agent choose the right provider tool rather than only listing names.

**Data flow**: The caller provides workspace, provider, and query text. The broker returns a `BrokerSearch` containing tools and optional guidance. The protocol defines the shape; broker implementations decide how smart the search is.

**Call relations**: Discovery or planning flows use this when a broker supports semantic search. The result can guide later calls to `schema` and `execute`.


##### `ConnectorBroker.credential`  (lines 200–200)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Describes how a broker provides a sync-safe credential for one connected account. Usually this is a proxy transport, meaning requests are routed through the broker and the real token stays server-side.

**Data flow**: The caller gives workspace, provider, and account handle. The broker checks or resolves that account and returns a `Credential`. The protocol itself only sets the expectation.

**Call relations**: `_credential` calls this when a feed-sync source uses a brokered account rather than a direct workspace key. `_BoundSourceCredentials.credential` then may wrap the returned transport with extra connection checks.


##### `RequestForwarder.forward`  (lines 219–221)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Describes how a broker forwards one HTTP request to a provider while adding the real account credential itself. This is used when the sandbox sends a request with a harmless sentinel value instead of a secret.

**Data flow**: The caller supplies the account ID, HTTP method, URL, headers, and body bytes. The implementation sends the real provider request through the broker and returns status, headers, and body in a `ForwardedResponse`.

**Call relations**: The egress proxy uses this interface when it intercepts a provider request that should be authenticated by a broker. It hands the result back to the sandbox as if it came from the provider.


##### `ConnectorResolver.transfer_hosts`  (lines 268–268)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Describes the broker file-store hosts that grants from an open connector namespace may contact. These hosts are allowed so uploads and downloads can work safely through the egress proxy.

**Data flow**: A resolver implementation returns a tuple of host names. There are no input arguments beyond the resolver itself, and no state is changed by the protocol property.

**Call relations**: Grant and egress setup can read this property when a resolver covers many possible providers. It complements provider routing with the network destinations needed for file transfer.


##### `ConnectorResolver.claims`  (lines 270–270)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Asks whether an open resolver is responsible for a provider slug. This prevents the system from treating the resolver as a blind catch-all when a direct workspace credential might be intended instead.

**Data flow**: The caller provides a provider name. The resolver checks its live catalog or rules and returns true or false. The protocol defines the question, not the lookup details.

**Call relations**: Code that chooses between brokered and direct provider handling can use this before routing through an open namespace. It supports registries that do not list every provider in advance.


##### `ConnectorResolver.entry`  (lines 272–272)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a registry entry for a provider that the resolver can serve, even if that provider was not explicitly registered. It gives the rest of the system a normal `ConnectorEntry` to route through.

**Data flow**: The caller supplies a provider slug. The resolver returns a `ConnectorEntry` containing that provider and the shared broker behind it. This method is expected to be a simple construction step.

**Call relations**: `ConnectorRegistry.entry` and `_credential` call this when no fixed entry exists but a resolver is installed. It lets open broker namespaces plug into the same execution path as explicit connectors.


##### `ConnectorResolver.catalog`  (lines 274–274)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches the resolver’s live service catalog for connectable providers. This lets the discovery tool show services that were not hard-coded into the registry.

**Data flow**: The caller sends search text and a maximum number of results. The resolver returns matching `CatalogEntry` values with provider slugs and labels. The protocol leaves the catalog source to the implementation.

**Call relations**: `ConnectorRegistry.search_catalog` delegates to this method when an open resolver exists. Its results are appended to the normal connector discovery view.


##### `ConnectorRegistry.entry`  (lines 291–297)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the broker entry for a provider. It first checks the explicitly installed connectors, then asks the open resolver if one is present, and fails loudly if nobody can serve that provider.

**Data flow**: It receives a provider name. It looks in the registry’s `entries` map; if found, it returns that entry. If not found and a resolver exists, it asks the resolver to build an entry. If neither path works, it raises a `KeyError`.

**Call relations**: Dynamic connector tools use this as the routing step before calling broker methods such as search, schema, or execute. It centralizes provider-to-broker selection.


##### `ConnectorRegistry.search_catalog`  (lines 299–304)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Gets extra provider discovery results from the open resolver, if one is installed. If there is no resolver, it safely returns an empty result.

**Data flow**: It receives query text and a result limit. If the registry has no resolver, it returns an empty tuple. Otherwise it forwards the query and limit to the resolver’s catalog search and returns those results.

**Call relations**: Discovery tools call this when building a list of connectable services. It adds live catalog results alongside explicitly registered connectors without making callers know whether a resolver exists.


##### `_credential`  (lines 307–324)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right authentication backend for a feed-sync source. Brokered accounts go to the provider’s broker, while the special direct account goes to the configured fallback auth backend.

**Data flow**: It takes a registry, workspace ID, provider name, and account handle. If the account is not the direct-account marker, it tries an explicit broker entry, then the resolver’s broker, and returns that broker’s credential. If the account is the direct marker, it asks the fallback auth proxy for a direct credential. If no suitable route exists, it raises an error.

**Call relations**: `_BoundSourceCredentials.credential` calls this after deciding whether the source is direct or connection-bound. This helper is the shared routing point that separates brokered credentials from direct workspace credentials.

*Call graph*: called by 1 (credential).


##### `_require_source_connection`  (lines 327–351)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Checks that a feed-sync source is still allowed to use a specific member-owned connection. This stops an old source from continuing to use an account after the connection was removed, replaced, or no longer matches.

**Data flow**: It receives workspace, connection, owner member, provider, and account identifiers. Inside the workspace context, it opens a database transaction and searches the `connection` table for an exact active match. If a matching row exists, it returns normally; if not, it raises a `ValueError` explaining that the connection is no longer active for the source.

**Call relations**: `_BoundSourceCredentials.credential` calls this before issuing brokered source credentials, and `_ConnectionTransport.handle_async_request` calls it before every proxied HTTP request. It relies on workspace database helpers and SQL selection to make the authorization check authoritative.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 363–371)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Sends one HTTP request through an existing broker transport, but only after re-checking that the source’s connection is still valid. This makes the safety check continuous, not just a one-time check when the sync starts.

**Data flow**: It receives an HTTP request object. First it calls `_require_source_connection` with the bound workspace, connection, owner, provider, and account. If the check passes, it forwards the request to the inner transport and returns the resulting HTTP response. If the check fails, the request is not sent.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper around broker-provided transports. During sync HTTP traffic, each request passes through this method before reaching the real broker transport.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 373–374)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is done. This releases any network resources held by the underlying transport.

**Data flow**: It takes no new data besides the wrapper object. It calls `aclose` on the inner transport and returns when that close operation completes. The wrapper itself does not add extra cleanup.

**Call relations**: HTTP client cleanup calls this as part of normal shutdown for the transport. It simply hands cleanup through to the broker transport it wraps.


##### `_BoundSourceCredentials.credential`  (lines 383–413)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Provides credentials for one feed-sync source while enforcing whether that source is allowed to use direct credentials or a specific brokered connection. It is the source-specific safety gate.

**Data flow**: It receives workspace, provider, and account. If the account is the direct marker, it rejects the request when this resolver was bound to a connection, otherwise it delegates to `_credential`. For a brokered account, it requires both connection and owner IDs, verifies the database connection with `_require_source_connection`, gets the broker credential through `_credential`, checks that it contains a proxy transport, then returns a new `Credential` whose transport is wrapped in `_ConnectionTransport` for ongoing checks.

**Call relations**: This object is created by `SourceCredentialResolver.bind` for a particular source run. Feed-sync code calls its `credential` method, and it coordinates `_require_source_connection`, `_credential`, and `_ConnectionTransport` to produce a credential that is both routed correctly and guarded against stale access.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 420–425)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an authentication proxy tied to one source’s connection information. This lets the sync runner ask for credentials later without repeatedly passing the connection IDs around.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those together with the registry into a `_BoundSourceCredentials` object and returns it as an `AuthProxy`. The registry is not changed.

**Call relations**: The sync runner uses this when preparing a source. The returned bound proxy later handles actual credential requests through `_BoundSourceCredentials.credential`.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/grants.py`

`domain_logic` · `request handling and cross-cutting grant lookup`

This file is the project’s “permission slip” system for external accounts. A member can start an OAuth connection flow, finish it after the browser redirects back, and end up with a durable record saying: this member owns this provider account, and this agent may use it. OAuth is a common web login-and-consent flow; here it is used so the system can connect to outside services without handing secrets to the agent.

The main flow has two halves. `ConnectFlow.authorize` creates a provider login link and hides important context inside a sealed `state` value, like putting the request details in a locked envelope. `ConnectFlow.complete` later opens that envelope, checks it has not expired or been changed, exchanges the returned code for a broker-side account, and asks `GrantStore` to record the connection and grant.

`GrantStore` is the database-facing part. It creates or reuses a member-owned connection, refuses to let another member take over the same provider account, lists active grants, revokes grants, toggles sharing, and disconnects accounts. Disconnecting also cleans up related feed sources and marks their pages as tombstoned, meaning “kept as a record but no longer active.”

The file also contains small helpers for stable names, sentinel values passed through command-line environments, and summary views used by user or operator surfaces.

#### Function details

##### `grant_sentinel`  (lines 36–40)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a predictable placeholder string for a connected account. The engine and proxy can both recognize this placeholder without separately registering it.

**Data flow**: It receives an account ID as text → prefixes it with a fixed marker → returns the combined sentinel string. It does not read or change stored data.

**Call relations**: This helper stands outside the database flow. It supports the path where an agent receives an environment value and the egress proxy later matches that value back to the brokered connection.


##### `OAuthProvider.provider`  (lines 86–86)

```
def provider(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose its provider name. That name is the stable label used when storing and checking connections.

**Data flow**: A concrete provider object supplies this property → callers read the provider name → that name is used in authorization, exchange, and grant records.

**Call relations**: The protocol is used by `ConnectFlow`, which treats all provider implementations the same way whether they come from the fixed provider map or a resolver.


##### `OAuthProvider.host`  (lines 89–89)

```
def host(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose the host that a grant permits. The host tells the proxy what outside destination this grant applies to.

**Data flow**: A concrete provider object supplies this property → callers read the host string → the grant store saves it with the connection.

**Call relations**: After `ConnectFlow.complete` exchanges the OAuth code, it passes this host into `GrantStore.record` so later egress checks know what the agent may reach.


##### `OAuthProvider.authorize_url`  (lines 91–91)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a provider builds the browser URL where a member gives consent. Each provider knows its own URL format.

**Data flow**: It takes sealed state and a redirect URI → combines them into the provider’s consent link → returns that link to be opened by the member.

**Call relations**: `ConnectFlow.authorize` calls this after choosing the provider descriptor, so the rest of the system does not need provider-specific URL rules.


##### `OAuthProvider.exchange`  (lines 93–95)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a provider turns the callback code into a connected broker account. This is where the provider confirms the account that was authorized.

**Data flow**: It receives the OAuth code, redirect URI, workspace ID, and state → asks or verifies with the provider or broker → returns an `OAuthAccount` containing the stable account ID.

**Call relations**: `ConnectFlow.complete` calls this during the callback leg before saving the grant. The returned account ID becomes the connection identity in `GrantStore.record`.


##### `OAuthProviderResolver.claims`  (lines 106–106)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open-ended provider resolver accepts a provider name. This prevents typos or unsupported provider slugs from silently creating useless links.

**Data flow**: It receives a provider name → validates it, possibly by checking a live catalog → returns true if this resolver can serve it, otherwise false.

**Call relations**: `ConnectFlow.validate_provider` uses this when the provider is not in the fixed provider map. It is the slower, authoritative check done before a connect request is accepted.


##### `OAuthProviderResolver.descriptor`  (lines 108–108)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Creates an OAuth provider descriptor for a provider name that belongs to an open connector namespace. It lets one broker extension serve many provider slugs.

**Data flow**: It receives a provider name → builds a descriptor object for that provider → returns it to the connect flow.

**Call relations**: `ConnectFlow._provider` calls this when no fixed provider descriptor exists but a resolver is installed.


##### `GrantStore.workspace_id`  (lines 183–184)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently active for database operations. A workspace is the project’s boundary for members, agents, connections, and grants.

**Data flow**: It reads the current workspace context → extracts its workspace ID → returns that ID.

**Call relations**: Most `GrantStore` methods use this property while reading or writing tables so a grant operation cannot accidentally affect another workspace.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 187–188)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent currently active for grant operations. This identifies which agent is being given or using access.

**Data flow**: It reads the current agent context → extracts its agent ID → returns that ID.

**Call relations**: `GrantStore.record`, `active_grants`, and grant mutation checks use this value so operations stay tied to the bound agent.

*Call graph*: 1 external calls (agent_current).


##### `GrantStore.record`  (lines 190–275)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> None
```

**Purpose**: Saves a completed connection and grants the current agent access to it. It also prevents one member from taking over another member’s already-connected account.

**Data flow**: It receives provider, account ID, host, grantor member, conversation, and sharing flag → rejects unsafe account IDs with control characters → creates the connection if missing or reuses it if present → verifies the same member owns it → updates the host → creates or updates the agent’s grant edge. The database is changed; the function returns nothing.

**Call relations**: `ConnectFlow.complete` calls this after the OAuth exchange succeeds. It is the point where a browser consent result becomes durable workspace permission.

*Call graph*: 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 277–314)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the external account grants available to the current agent. This tells runtime code what connections the agent may use.

**Data flow**: It reads the current workspace and agent → queries grant rows joined with their connection rows → turns each row into a `Grant` object → returns all grants as a tuple.

**Call relations**: It is called by `core/src/ufo/loop/queue._grant_cli_env`, which needs these grants when preparing the agent’s command-line environment.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 316–328)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant from the current agent, if the requesting member is allowed to do so. Revoking the grant stops that agent from using the connection but does not necessarily delete the connection itself.

**Data flow**: It receives a grant ID and actor member ID → checks the actor’s permission through `_grant_for_actor` → deletes the grant row if allowed → returns true if a row was deleted, false if there was no matching grant.

**Call relations**: This is a public mutation method. It relies on `_grant_for_actor` to do the ownership and admin checks before touching the database.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.set_shared`  (lines 330–355)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether a grant is marked as shared. It enforces who may make that change, including a stricter rule when turning sharing on.

**Data flow**: It receives a grant ID, the desired shared value, and actor member ID → checks whether the actor may mutate that grant → updates the shared flag and timestamp → returns true if the update happened, false if the grant was not found.

**Call relations**: Like `revoke`, it delegates permission checking to `_grant_for_actor`. It then writes the final change to the connector grant table.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (update, workspace_tx).


##### `GrantStore.disconnect`  (lines 357–413)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Deletes a member-owned connection and cleans up data that depended on it. This is stronger than revoking one grant because it removes the connection itself.

**Data flow**: It receives a connection ID and actor member ID → checks permission through `_connection_for_actor` → finds feed sources attached to the connection → removes source grants, detaches and marks those sources removed, tombstones related pages, and deletes the connection row. It returns true if the connection was found and removed, false if not found.

**Call relations**: This method is used when a member or permitted admin disconnects an account. Connector grant edges disappear through database cascade after the connection is deleted.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 415–452)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific connection. The owner may change it, and an admin may change it when admin changes are allowed.

**Data flow**: It receives an open database connection, a connection ID, an actor member ID, and an admin-allowed flag → locks and reads the connection row → returns none if missing → returns the connection ID if the actor owns it → otherwise checks whether the actor is an admin → either returns the ID or raises a permission error.

**Call relations**: `GrantStore.disconnect` calls this directly. `_grant_for_actor` also calls it because grant permissions depend on the ownership of the underlying connection.

*Call graph*: called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._grant_for_actor`  (lines 454–492)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific grant for the current agent. It first finds the grant’s connection, then reuses the connection permission rules.

**Data flow**: It receives an open database connection, grant ID, actor member ID, and admin-allowed flag → finds the grant’s connection for the current workspace and agent → asks `_connection_for_actor` whether the actor may mutate that connection → locks and confirms the grant row → returns the grant ID or none.

**Call relations**: `GrantStore.revoke` and `GrantStore.set_shared` call this before deleting or updating a grant. It centralizes the safety checks for grant changes.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 510–530)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Starts the OAuth connect flow by creating the provider consent URL. It seals all the important context into the OAuth state value so the callback can later be trusted without a pending database row.

**Data flow**: It receives workspace, agent, provider, grantor member, conversation, and sharing choice → finds the provider descriptor → builds a `ConnectState` object → encrypts it with Fernet, a symmetric encryption tool → asks the provider for an authorization URL → returns that URL.

**Call relations**: `ConnectHandoff.authorize` calls this when a terminal connect request needs a browser link. The returned URL is stored and shown to the member.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 532–537)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Confirms that a requested provider is available before a connect request is accepted. This gives a clear error for unknown or misspelled providers.

**Data flow**: It receives a provider name → checks the installed provider map → if needed, asks the resolver whether it claims the provider → returns nothing if valid or raises `UnknownProvider` if not.

**Call relations**: This is the full validation step for new connect requests. It may call an external resolver, unlike the cheaper `knows_provider` check.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 539–544)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly checks whether the connect machinery could still serve a provider. It is intentionally cheap and does not call an external catalog.

**Data flow**: It receives a provider name → checks if it is in the installed provider map or whether any resolver exists → returns true or false.

**Call relations**: `ConnectHandoff.authorize` uses this while holding a database row lock, where it should avoid slow external validation.


##### `ConnectFlow.bridge_workspace`  (lines 546–552)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and tells the caller which workspace it belongs to. This prevents a bridge callback from running under the wrong workspace.

**Data flow**: It receives sealed state, provider name, and callback URI → opens and validates the sealed state → checks the provider and callback match what was sealed and configured → confirms the provider exists → returns the workspace ID or raises an invalid-state error.

**Call relations**: `connect_bridge_workspace` calls this through the installed global flow. It is a gatekeeper for bridge requests before they are accepted.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 554–569)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth flow after the provider redirects back with a code. It verifies the sealed state, exchanges the code for an account, and records the grant.

**Data flow**: It receives sealed state and OAuth code → decrypts and validates the state → finds the provider descriptor → temporarily enters the sealed workspace and agent contexts → exchanges the code for an account ID → records the connection and grant through `GrantStore.record` → returns a `GrantRecorded` summary.

**Call relations**: This is the callback-side partner to `authorize`. It hands off to the provider for token/account exchange and to `GrantStore` for durable permission storage.

*Call graph*: calls 2 internal fn (_open, _provider); 3 external calls (__init__, agent, ws).


##### `ConnectFlow._provider`  (lines 571–577)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the provider descriptor used by the connect flow. It hides whether the provider came from a fixed map or an open resolver.

**Data flow**: It receives a provider name → looks in the installed providers → if absent, asks the resolver to create a descriptor → returns the descriptor or raises `UnknownProvider`.

**Call relations**: `ConnectFlow.authorize`, `bridge_workspace`, and `complete` all call this before doing provider-specific work.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 579–584)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. It rejects state that was changed, cannot be read, or is older than the allowed time window.

**Data flow**: It receives the state string from a URL → asks Fernet to decrypt it with a time-to-live limit → parses the JSON into `ConnectState` → returns the claims, or raises `ConnectStateInvalid` on failure.

**Call relations**: `ConnectFlow.bridge_workspace` and `ConnectFlow.complete` both call this before trusting any callback or bridge request information.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 593–675)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a private terminal connect request into a reusable OAuth URL for the member who requested it. It memoizes the URL so repeated clicks or retries use the same authorization link while it is fresh.

**Data flow**: It receives workspace ID, turn ID, and member ID → locks the turn row → verifies the turn still contains a connect request, belongs to that member, names an available provider, and has not expired → returns an existing still-fresh URL if present → otherwise asks `ConnectFlow.authorize` for a new URL, stores it on the turn, and returns it.

**Call relations**: This sits between a user-facing surface and `ConnectFlow.authorize`. It protects private connect links by checking the requesting member and by using the turn row as the single source of truth.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 681–689)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow. This gives tools and callbacks one shared place to find the OAuth setup for this running server.

**Data flow**: It receives a `ConnectFlow` or none → stores it in a module-level variable → returns nothing. Passing none means grants are unavailable.

**Call relations**: This is called during server setup or tests. Later, `installed_connect_flow` reads the value when connect-related requests arrive.


##### `installed_connect_flow`  (lines 692–695)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed connect flow or raises a clear error if connect support is not configured. This avoids silent failures when no credential key was set.

**Data flow**: It reads the module-level installed flow → returns it if present → otherwise raises `ConnectUnavailable`.

**Call relations**: `connect_bridge_workspace` calls this before validating a bridge request. Other connect surfaces can use it as the shared access point.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 698–707)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming browser bridge request is valid and, if so, identifies its workspace. Invalid requests return none instead of leaking details.

**Data flow**: It receives a Starlette `Request` object → reads `state`, `provider`, and `callback` query parameters → asks the installed connect flow to verify them → returns the workspace ID if accepted, or none if unavailable, invalid, or unknown.

**Call relations**: This is a small request-facing wrapper around `ConnectFlow.bridge_workspace`. It converts connect-flow exceptions into a simple accept-or-reject answer.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 713–720)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Builds a stable, human-readable object name for a provider account. The short digest at the end keeps names distinct even when cleaned-up provider or account text would otherwise collide.

**Data flow**: It receives provider and account ID → hashes the exact pair → slugifies both text parts into URL-safe lowercase chunks → appends a short hash qualifier → returns the final name.

**Call relations**: It calls `_slug` for the readable parts and `hashlib.sha256` for the collision-resistant qualifier. Other surfaces can use this so they all name the same account consistently.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 723–724)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a simple lowercase slug suitable for names. A slug is text cleaned for use in identifiers or URLs.

**Data flow**: It receives raw text → lowercases it → replaces runs of non-letter-or-number characters with hyphens → trims leading and trailing hyphens → returns the cleaned string.

**Call relations**: `account_object_name` calls this for both the provider and account pieces before adding the digest.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 727–734)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-friendly summaries of connector grants for the current agent. This is useful for showing what accounts the agent can use.

**Data flow**: It reads the current workspace and current agent → builds a filter for just that agent’s grants → delegates the query to `_grant_summaries` → returns summary objects.

**Call relations**: This is the agent-scoped public wrapper around `_grant_summaries`. It supplies the current-context filter so the shared query helper can do the database work.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, agent_current, ws_current).


##### `workspace_grant_summaries`  (lines 737–740)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-friendly summaries of all connector grants in a workspace. This is intended for an operator or workspace-wide view rather than one agent’s view.

**Data flow**: It receives a workspace ID → enters that workspace context → asks `_grant_summaries` for all grants in that workspace → returns summary objects.

**Call relations**: This is the workspace-wide wrapper around `_grant_summaries`. It sets the workspace context so database access is scoped correctly.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 743–783)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into readable audit summaries. It joins grant, connection, and agent information into one view.

**Data flow**: It receives a database filter describing the desired scope → queries connector grants joined to connections and agents → orders by provider and agent name → converts each row into a `GrantSummary` → returns all summaries.

**Call relations**: Both `grant_summaries` and `workspace_grant_summaries` call this with different filters. It keeps the summary-building logic in one place.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 3 external calls (__init__, select, workspace_tx).


##### `connection_summaries`  (lines 786–848)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the member-owned connections in the current workspace and shows which agents currently have grants to each one. This is a connection-centered view rather than a grant-centered view.

**Data flow**: It reads the current workspace → queries connections with optional grant and agent joins → groups rows by provider and account ID → collects agent names for each connection → returns `ConnectionSummary` objects with sorted agent lists.

**Call relations**: This function is used by surfaces that need to show connected accounts themselves, including accounts with no current agent grants.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling during OAuth account connection`

This file is a small web doorway for account connection. When a user starts a “connect account” flow in chat, the system sends them to an outside provider, such as an OAuth service, to approve access. OAuth is a common sign-in-and-permission handoff: the outside provider redirects the browser back with a short-lived code and a protected state value. The state value is like a sealed claim ticket; it proves which conversation, agent, and member started the connection.

The file creates a FastAPI router, which is a group of web routes, under `/v1`. It exposes `/v1/connect/callback` as the place providers redirect to. When a request arrives, the code first asks for the installed connect flow. If connecting is not available, it returns a 503 error, meaning the service cannot do this right now. It then checks that both required pieces, `state` and `code`, are present. If either is missing, it returns a 400 error, meaning the request is bad.

If the inputs are present, it asks the connect flow to complete the handoff. That verifies the sealed state, trades the provider code for the account connection, and records the grant. If the state is fake or expired, the user gets a 400 error. If the provider is not installed, they get a 404 error. On success, the response is plain text telling the user the account was connected and to return to chat.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP endpoint that finishes an account connection after the provider redirects the browser back. It checks the returned state and code, completes the connection, and gives the user a simple success or error message.

**Data flow**: The function receives `state` and `code` query values from the browser request. It first gets the current connect-flow object, then rejects the request if either value is missing. It passes the state and code into the flow’s completion step, which verifies the state and records the connected account. On success, it returns a plain text message naming the connected provider and account; on known failure cases, it turns them into clear HTTP errors.

**Call relations**: FastAPI calls this function when a browser visits `/v1/connect/callback`. Inside the request, it calls `ufo.grants.installed_connect_flow` to find the connection machinery, raises `fastapi.HTTPException` when the request cannot be completed, and uses `fastapi.responses.PlainTextResponse` to send the final human-readable success message back to the browser.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### Composio bridge
Composio integration files expose hosted consent, dynamic connector resolution, tool discovery, action execution, file handling, and proxied provider requests.

### `extensions/composio/ufo_ext_composio/broker.py`

`domain_logic` · `request handling`

ComposioBroker is the “front desk” for all Composio-backed connectors. The rest of the system asks it ordinary questions like “what tools exist for this provider?”, “what inputs does this tool need?”, or “please run this tool for this workspace account.” The broker then talks to Composio and translates the answers into UFO’s own connector shapes.

A key safety idea in this file is that each call looks up the Composio client fresh, instead of keeping one stored forever. That means tests can swap the transport layer, and no old connection settings leak between calls.

The file also protects users from confusing failures. If Composio says a tool slug was not found, the broker tries to discover real tool slugs and adds them to the error, so the next attempt can be better informed. If the failure instead looks like a missing connected account, it gives reconnect guidance rather than pretending the problem is the tool name.

Files get special treatment. Tool schemas are adjusted so file inputs use UFO’s workspace-file vocabulary. Tool outputs are searched for Composio file objects, which contain presigned download URLs. Uploads are staged by asking Composio for a temporary upload slot. Credentials are never returned as raw tokens; instead, this file returns a proxy transport that routes provider HTTP through Composio.

#### Function details

##### `ComposioBroker.tools`  (lines 48–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds available Composio tools for a provider, optionally narrowed by a search query. It returns them in UFO’s standard BrokerTool form so the rest of the connector system does not need to understand Composio’s raw response.

**Data flow**: It receives a workspace id, provider name, and query text. It asks the current Composio client to list matching tools, then passes the raw list through a small translator. The result is a tuple of simplified tool records with slugs and short descriptions.

**Call relations**: This is the discovery entry point for Composio connector tools. It calls the Composio client for the catalog, then hands the response to _discovered_tools so callers get clean BrokerTool objects instead of Composio-specific JSON.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–65)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the input schema for one tool, meaning the description of what arguments that tool accepts. It also rewrites Composio file-upload fields into the file format expected by UFO workspace tools.

**Data flow**: It receives a workspace id, provider name, and tool slug. It asks Composio for that tool’s schema, converts file-upload parts of the input schema, and returns a BrokerTool containing the slug, description, and input schema. If Composio says the slug does not exist, it raises UnknownBrokerTool.

**Call relations**: This is used when the system needs exact instructions for calling a tool. It gets the raw schema from the Composio client, sends the input shape through workspace_file_schema, and packages the answer as a BrokerTool for the connector layer.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 67–90)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for a specific workspace and connected account. It also turns two common failure cases into more helpful errors: a stale account tells the user to reconnect, and a missing tool slug may include real available tool names.

**Data flow**: It receives the workspace id, provider, tool slug, argument values, connected account id, and an optional idempotency key, which is a repeat-safe request identifier. It builds the Composio broker-user id from the workspace id, sends the execution request, and returns Composio’s response dictionary. If execution fails, it inspects the error and either raises reconnect guidance, a better slug-miss error, or the original error.

**Call relations**: This is the main run path for tools. It calls the Composio client to execute the tool, uses _stale_account to recognize a missing connected account, uses _reconnect_error to explain reconnect steps, and calls _slug_miss when a 404 may mean the model used the wrong tool slug.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 92–97)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a tool execution response. Composio may place file objects deep inside nested response data, so this function searches the whole response instead of only looking at the top level.

**Data flow**: It receives a response dictionary from a tool run. It creates an empty list, asks _collect_files to walk through the response, and returns all discovered files as BrokerFile objects in a tuple.

**Call relations**: This is called after tool execution when the connector layer needs to expose downloadable results. It delegates the recursive searching work to _collect_files, which recognizes Composio’s file shape.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 99–115)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a temporary upload location for a file that will be used as a tool input. This lets the sandbox upload bytes to Composio’s file store before the tool is executed.

**Data flow**: It receives the workspace id, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL to upload to, the content type to use, and the argument object the later tool call should include.

**Call relations**: This is used before executing tools that accept files. It relies on the Composio client to mint the upload destination, then packages the returned key into the standard StagedUpload shape expected by UFO’s dynamic connector tools.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 117–120)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for connector tools using Composio’s Tool Router, which is a search layer meant to find relevant tools from natural query text. It returns the result in UFO’s BrokerSearch format.

**Data flow**: It receives a workspace id, provider, and query. It gets the current Composio client and passes all of that to search_connector_tools. The output is the search result returned by that helper.

**Call relations**: This is the higher-level search path, separate from simply listing tools. It hands the work to Composio’s search_connector_tools helper, which uses the active Composio client and the workspace context.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 122–138)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe credential object for making provider HTTP calls through Composio, without giving the caller a raw token. It first confirms the connected account belongs to this workspace’s broker user, which prevents one workspace from accidentally using another workspace’s account.

**Data flow**: It receives a workspace id, provider, and connected account id. It builds the expected broker-user id, asks Composio to verify the account, and if valid returns a Credential whose transport proxies requests through Composio. If Composio says the account is missing, it raises an error with reconnect guidance.

**Call relations**: This is used when a connector needs provider-style HTTP access rather than a one-shot tool execution. It calls the Composio client for ownership verification, uses _reconnect_error for stale accounts, builds a ComposioProxyTransport, and wraps that transport in a Credential.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 140–161)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by trying to include real tool slugs available for the provider. This helps the next attempt choose a valid tool name instead of repeating the same bad slug.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the bad slug into search words, asks Composio for matching tools, and falls back to listing all tools if needed. If it finds tools, it returns a new ComposioError whose message includes the available slugs; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a 404 for tool execution. It uses _discovered_tools to translate discovery results, and it deliberately treats this as best effort: if discovery fails, the original execution error is preserved.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 164–173)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through nested response data and collects Composio file outputs. A Composio file is recognized by a small object containing a name, MIME type, and s3url download link.

**Data flow**: It receives any value from a response and a list being filled. If the value looks like a Composio file object with a non-empty URL, it appends a BrokerFile. If the value is a dictionary or list, it recursively checks each contained item. It does not return a value; it changes the provided list.

**Call relations**: ComposioBroker.file_outputs starts the search by passing the full response into this helper. This helper does the deep walk and creates BrokerFile records whenever it finds matching file objects.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 176–187)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error probably means the connected account is no longer available. This distinction matters because a missing account should tell the member to reconnect, not suggest different tool names.

**Data flow**: It receives a Composio error and the account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account: Composio’s own phrase “connected account” with “not found,” or the specific account id with “not found.” It returns true if the error matches that stale-account pattern, otherwise false.

**Call relations**: ComposioBroker.execute uses this before treating a 404 as a possible missing tool slug. That ordering keeps dead account grants from being misreported as tool discovery problems.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 190–191)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a clearer Composio error that tells the user the account should be reconnected. It keeps the original error status and message, then adds provider-specific reconnect guidance.

**Data flow**: It receives the original Composio error and provider name. It asks stale_grant_guidance for a human-facing reconnect message, appends that to the original error body, and returns a new ComposioError with the same status.

**Call relations**: ComposioBroker.execute calls this when a tool run appears to use a stale connected account. ComposioBroker.credential also calls it when account ownership verification fails because the account is not found.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 194–216)

```
def _discovered_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Translates Composio’s raw tool-list response into UFO’s simpler BrokerTool records. It filters out malformed entries and shortens long descriptions so discovery results stay compact.

**Data flow**: It receives a dictionary returned by Composio list_tools. It reads the items list, skips anything that is not a valid dictionary with a usable slug or name, copies a short description when present, and returns a tuple of BrokerTool objects. If the response does not contain a proper items list, it returns an empty tuple.

**Call relations**: ComposioBroker.tools uses this for normal tool discovery. ComposioBroker._slug_miss also uses it when trying to turn a missing-slug error into a more helpful message with real available tool slugs.

*Call graph*: called by 2 (_slug_miss, tools); 1 external calls (__init__).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling for connector connection, discovery, upload, search, and execution`

Composio acts like a secure front desk for many outside services. Instead of UFO storing a GitHub or Google token itself, Composio keeps that secret and UFO stores only a connected-account id. This file contains the client code that talks to Composio's web API and turns Composio responses into the simpler shapes UFO expects.

The flow starts with connection: the client finds or creates an auth configuration, asks Composio for a hosted OAuth link, and later verifies that the returned connected account belongs to the right workspace user, is active, and matches the requested toolkit. For discovery, it can list connectable toolkits, list tools inside one toolkit, fetch a single tool schema, or use Composio's Tool Router for semantic search. Tool Router search results are cleaned up into UFO's broker search format, including suggested plan steps and warnings.

For execution, the file sends tool calls to Composio's server-side execute endpoint. It also limits argument size so huge payloads are rejected before they leave the process. For tools that need files, it asks Composio for an upload slot and returns the storage key plus a temporary upload URL. A few helpers keep the API strict: bad response shapes become explicit errors, unsafe toolkit slugs are rejected, and known unusable providers are filtered out.

#### Function details

##### `connectable`  (lines 120–144)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit is safe and useful for this deployment to offer to users. It blocks known-bad providers, providers without Composio-managed login support, and providers that expose no tools.

**Data flow**: It receives a toolkit slug and a catalog record from Composio. It checks the slug against the local banned list, then reads the record for managed authentication schemes and a positive tool count. It returns true only when all of those checks pass.

**Call relations**: When the client checks a single toolkit or lists search results from the toolkit catalog, those paths call this function before showing the toolkit to a user. It is the local gatekeeper that prevents the rest of the connection flow from offering a dead or unsafe grant.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 151–154)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception when Composio fails or returns data this client cannot safely use. It preserves both the status code and response body so callers can report or inspect what went wrong.

**Data flow**: It receives a numeric status and a text body. It formats them into a readable error message and stores both values on the exception object. The result is an exception that can be raised instead of silently continuing with bad data.

**Call relations**: Many client methods raise this when a Composio response is missing required fields, belongs to the wrong user, has the wrong toolkit, or reports an HTTP error. It is the shared failure language for this file.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 171–180)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to connect an outside service through Composio. This is the start of the consent process, similar to sending someone to a secure login page.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first gets an auth configuration for that toolkit, then posts those details to Composio's connected-account link endpoint. It returns the redirect URL Composio provides, or raises an error if no usable URL comes back.

**Call relations**: This method calls the auth-configuration helper before making the link request. It uses the shared POST helper to talk to Composio and the shared error type when the response shape is wrong.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 182–206)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Verifies that a connected account id is legitimate for this workspace and toolkit. This prevents one user or one connector from accidentally or maliciously using another account's grant.

**Data flow**: It receives an account id, the expected broker user id, and the expected toolkit slug. It fetches the account record from Composio, checks ownership, checks that the account is active, and checks that the authenticated toolkit matches. It returns an OAuthAccount containing the account id when all checks pass.

**Call relations**: This method relies on the shared GET helper for the Composio call and raises ComposioError for ownership, status, or toolkit mismatches. It is used after the consent handoff to turn a Composio connected account into UFO's stored account reference.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.list_tools`  (lines 208–214)

```
async def list_tools(self, toolkit: str, query: str='', limit: int=TOOL_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Asks Composio for tools available inside a toolkit, optionally narrowed by a search query. It is used when UFO needs a catalog of what a connected service can do.

**Data flow**: It receives a toolkit slug, optional search text, and a result limit. It builds query parameters and sends a GET request to Composio's tools endpoint. It returns Composio's response as a dictionary.

**Call relations**: The broker uses this when it needs to resolve or search for tool slugs. This method delegates the actual HTTP work and response checking to the shared GET helper.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 216–217)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed schema for one Composio tool. A schema describes what inputs the tool accepts, like a form describing its fields.

**Data flow**: It receives a tool slug. It sends a GET request for that specific tool and returns the response dictionary from Composio. It does not transform the schema itself.

**Call relations**: This is a thin catalog lookup built on the shared GET helper. Other parts of the connector system can call it when they need to describe one tool before execution.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 219–236)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a user-supplied toolkit slug names a real Composio toolkit this deployment is willing to broker. It also returns the friendly display name when the toolkit is allowed.

**Data flow**: It receives a slug. It first rejects unsafe characters so the slug cannot be used to reach other API paths, then fetches the toolkit record. A missing toolkit returns None; any other Composio error is re-raised. If the toolkit passes the local connectable check, it returns the toolkit name or the slug as a fallback.

**Call relations**: This method combines the shared GET helper with the connectable gate. It is part of the resolver path that decides whether an open-ended connector name can be claimed by Composio.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 238–256)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio's toolkit catalog and returns only the services this deployment can actually offer. This powers discovery without relying only on a fixed built-in connector list.

**Data flow**: It receives search text and a limit. It requests matching toolkits from Composio, walks through the returned items, skips malformed or non-connectable entries, and returns pairs of toolkit slug and display label.

**Call relations**: This method calls the shared GET helper to read the catalog and calls connectable for each item. It feeds user-facing discovery while keeping unusable providers out of the results.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 258–272)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool through Composio's server-side execute API. This lets Composio inject the stored provider token itself, so UFO does not handle the secret.

**Data flow**: It receives a tool slug, arguments, a broker user id, an optional connected-account id, and an optional idempotency key. It builds the request body, rejects it if the JSON would be larger than the configured size limit, adds an idempotency header when provided, and posts the request. It returns Composio's execution response.

**Call relations**: This method uses the shared POST helper for the actual network call. It sits on the execution path after discovery and account connection have already identified which tool and account to use.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 274–298)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio where a file should be uploaded before a tool call uses it. It returns both the tool-facing storage key and, when needed, a temporary URL for uploading the bytes.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts an upload request to Composio, checks that a storage key is present, and reads the temporary upload URL if Composio provides one. It returns a ComposioUpload object, with no upload URL when Composio says the same file already exists.

**Call relations**: This method uses the shared POST helper and raises ComposioError for malformed upload responses. It supports the file-staging path used before executing tools that accept file inputs.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 300–311)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The session provides an MCP endpoint, which is a tool-calling interface used here only for searching.

**Data flow**: It receives a broker user id and a list of toolkit slugs. It posts a session request to Composio with those toolkits enabled, then checks that the response includes both a session id and an MCP URL. It returns those two values as a ToolRouterSession.

**Call relations**: The search_connector_tools function calls this when no cached search session exists for the user and connector. This method uses the shared POST helper and raises ComposioError if Composio does not return the endpoint needed for search.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 313–331)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication setup that should be used for a toolkit, creating a Composio-managed one if needed. This lets operator-created custom configs win, while still making ordinary toolkits connectable.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts the first id if present. If none exists, it posts a request to create a managed auth config and returns the new id. It raises an error if the created response does not contain an id.

**Call relations**: connect_link calls this before asking for a user-facing consent link. This helper uses both shared GET and POST helpers, plus _auth_config_id to read the existing-config response.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 333–335)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a GET request to Composio and turns the response into a checked dictionary. It is the common read path for this client.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the GET request, and passes the response to the shared body parser. It returns the parsed dictionary or raises a ComposioError.

**Call relations**: Catalog reads, account verification, toolkit checks, and auth-config lookup all call this helper. It relies on _http to create the configured client and _body to enforce response rules.

*Call graph*: calls 2 internal fn (_http, _body); called by 6 (_auth_config, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 337–341)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a POST request to Composio and turns the response into a checked dictionary. It is the common write or action path for this client.

**Data flow**: It receives an API path, a JSON body, and optional headers. It opens an HTTP client, sends the POST request, and passes the response to the shared body parser. It returns the parsed dictionary or raises a ComposioError.

**Call relations**: Connection links, auth-config creation, tool execution, upload-slot creation, and Tool Router session creation all use this helper. It relies on _http for the configured connection and _body for validation.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 343–349)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the configured asynchronous HTTP client used for one Composio API call. It attaches the API base URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the ComposioClient's api_key and optional transport. It creates an httpx AsyncClient with the fixed Composio base URL and timeout. It returns that client to be used inside a short-lived request block.

**Call relations**: The shared GET and POST helpers call this whenever they need to contact Composio. Keeping this in one place makes every API call use the same authentication and timeout behavior.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 352–360)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Checks and parses an HTTP response from Composio. It turns bad statuses and unexpected response shapes into explicit errors.

**Data flow**: It receives an httpx response. If the status code is an error, it raises ComposioError with the response text. If the response has no content, it returns an empty dictionary. Otherwise it parses JSON and requires the result to be an object-like dictionary.

**Call relations**: Both _get and _post send every response here. This function is the guardrail that stops callers from treating an error page, list, string, or malformed response as a valid API result.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 363–389)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the simpler file language UFO expects from the agent. Instead of asking the model for internal storage fields, it asks for a workspace file path.

**Data flow**: It receives any schema value. If it finds a dictionary marked as file-uploadable, it replaces it with an object requiring the workspace_file path field and preserves a description when present. For nested dictionaries and lists, it recursively rewrites their contents. Other values pass through unchanged.

**Call relations**: _search_result calls this while converting Tool Router search results into BrokerTool objects. It keeps the model-facing tool schema focused on files visible inside the workspace, while the broker later handles Composio's storage details.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 392–399)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small helper for deciding whether a toolkit already has a usable auth setup.

**Data flow**: It receives a response dictionary. It looks for an items list and returns the id from the first dictionary item that has a string id. If the shape is missing or no id is found, it returns None.

**Call relations**: _auth_config calls this after listing existing auth configs. A returned id lets the client reuse the existing setup; None tells the client to create a managed one.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 402–409)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the deployment's default Composio client from the environment. It fails loudly if the Composio API key is not configured.

**Data flow**: It reads the COMPOSIO_API_KEY environment variable. If it is missing or empty, it raises a runtime error. Otherwise it returns a ComposioClient initialized with that key.

**Call relations**: Other parts of the extension can call this when they need the standard broker client. It centralizes the rule that connector OAuth cannot run without a configured Composio key.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 416–417)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. It avoids repeated type checks in search-result conversion.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: _search_result uses this repeatedly while reading optional nested fields from Tool Router output. It keeps malformed or missing sections from crashing the conversion.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 420–423)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It is used for fields like tool slugs, plan steps, and warnings.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only items that are non-empty strings and returns them as a tuple.

**Call relations**: _search_result uses this to normalize Tool Router fields that should contain lists of strings. This keeps the final BrokerSearch clean even when the upstream response contains odd values.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 426–460)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router search output into UFO's BrokerSearch format. It gathers matched tools, input schemas, suggested plan steps, guidance, and pitfalls into one clean result.

**Data flow**: It receives a raw Tool Router result dictionary. It finds the nested data and tool schemas, walks each result entry, collects primary and related tool slugs without duplicates, rewrites file-upload schemas into workspace-file schemas, and gathers plan, guidance, and warning text. It returns a BrokerSearch containing BrokerTool entries and supporting advice.

**Call relations**: search_connector_tools calls this after the MCP search call returns. This function is the translator between Composio's search response shape and the broker-facing structure used by the rest of UFO.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 463–486)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Runs semantic search for tools inside one connector using Composio's Tool Router. It finds tools by use case, not just by exact name.

**Data flow**: It receives a Composio client, workspace id, connector slug, and natural-language query. It builds the broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the router's search tool over MCP with the query, and converts the result into BrokerSearch. It updates the in-memory session cache when a new session is opened.

**Call relations**: This function calls tool_router_session when the cache does not already have a session, then hands the actual search request to mcp_session.mcp_call_tool. After Composio answers, it delegates cleanup and translation to _search_result.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect flow`

Composio is a service that connects to many outside apps and runs their tools on UFO’s behalf. This file is the bridge between UFO’s connector system and that broad Composio catalog. Without it, UFO would need a separate built-in connector entry for every Composio-supported app, and newly added Composio apps would not be discoverable automatically.

The central piece is `ComposioResolver`. It is small and mostly stateless: it keeps only a shared `ConnectorBroker`, which is the object later used to run connector work. When UFO sees a provider name that no explicitly registered connector claimed, this resolver can ask, “Is this a Composio toolkit slug?” A slug is a short machine-friendly name, like an app identifier.

The resolver first rejects locally banned names, then asks Composio’s live catalog whether the toolkit can actually be connected. If it can, the resolver builds the OAuth description UFO needs for the connection flow. OAuth is the standard “grant this app access” login process. Here, the real account token stays with Composio, and tools run through Composio rather than directly against the provider’s own host.

It also supports search: given a query, it asks Composio for matching connectable toolkits and returns clean catalog entries UFO can show to users.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells UFO which Composio file-transfer hosts are allowed for brokered tool runs. That matters because tools may need to send or receive files, and the sandbox must know which outside hosts are expected.

**Data flow**: It reads the shared `COMPOSIO_TRANSFER_HOSTS` list from the Composio client module and returns it as the allowed host tuple. It does not change anything.

**Call relations**: When connector code needs to know what file-transfer destinations are safe for a Composio-backed grant, it asks this resolver. The resolver simply exposes the predefined Composio hosts used by the rest of the Composio transport flow.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function answers the question, “Can Composio provide a connector for this provider name?” It protects the system by rejecting banned names locally before asking Composio’s live catalog.

**Data flow**: It receives a provider slug as text. First it lowercases the slug and checks it against Composio’s banned list; if it is banned, the result is `False`. Otherwise it creates or retrieves a Composio client with `ufo_ext_composio.client.composio_client`, asks whether the toolkit is connectable, and returns `True` only if Composio finds a matching connectable toolkit.

**Call relations**: During connector resolution, this is the gatekeeper for provider names that were not claimed by explicit built-in connectors. If the local ban check passes, it hands the question to the Composio client so the decision is based on Composio’s current catalog rather than a stale hard-coded list.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth description UFO needs to start connecting a Composio-backed provider. It says, in effect, “connect this provider through Composio, not through a provider-specific host inside UFO.”

**Data flow**: It receives a provider slug and uses it to create a `ComposioOAuthProvider`. The host is set to an empty string because Composio owns the remote connection details and keeps the account token on its side. The newly created OAuth provider description is returned.

**Call relations**: After a provider slug has been accepted, the connect flow asks for a descriptor. This function hands off to `ComposioOAuthProvider.__init__` to create the object that the rest of UFO’s OAuth connection machinery can use.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the connector registry entry for a Composio-backed provider. It gives UFO a readable label and points all such providers at the same shared Composio broker.

**Data flow**: It receives a provider slug. It turns underscores into spaces and title-cases the result for a human-friendly label, then creates a `ConnectorEntry` containing the original provider slug, that label, and this resolver’s shared broker. The `ConnectorEntry` is returned.

**Call relations**: Once the resolver has claimed a provider, the registry needs an entry it can use like any other connector entry. This function builds that entry by calling `ConnectorEntry.__init__`, routing the specific provider name to the common `ComposioBroker` stored on the resolver.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–50)

```
async def catalog(self, query: str, limit: int=TOOL_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: This function searches Composio’s toolkit catalog and returns provider choices UFO can show to a user. It helps discovery avoid suggesting services that cannot actually be connected.

**Data flow**: It receives a search query and an optional maximum number of results. It gets a Composio client with `ufo_ext_composio.client.composio_client`, asks Composio for matching toolkits, then turns each returned slug and label into a `CatalogEntry`. It returns those entries as an immutable tuple.

**Call relations**: When a discovery tool or UI wants to search for Composio-backed connectors, it calls this function. The resolver delegates the live search to the Composio client, then wraps each result with `CatalogEntry.__init__` so the rest of UFO receives catalog items in its normal connector format.

*Call graph*: 2 external calls (__init__, composio_client).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Some connected services need secret credentials, such as access tokens. In this setup, Composio keeps those secrets and injects them on its own servers. That means this project cannot simply call the provider directly. This file is the bridge: it accepts an ordinary HTTP request meant for a provider, repackages it as a Composio `POST /tools/execute/proxy` call, and includes the connected account id so Composio knows which credential to use.

The main piece, `ComposioProxyTransport`, acts like a custom road for an `httpx` HTTP client. Instead of driving straight to the provider, it detours through Composio. It copies the request method, URL, query parameters, safe headers, and body into a JSON payload. It deliberately leaves out headers like `authorization`, `host`, and `content-length`, because those either belong to the original connection or could conflict with Composio's own request.

When Composio replies, the file rebuilds a normal HTTP response for the caller. This matters because existing connector code can keep using familiar HTTP behavior, including status codes, headers, and pagination headers. For large non-JSON binary responses, Composio may store the bytes elsewhere and return a link instead; this file turns that into a redirect, avoiding huge memory use in the proxy process.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 63–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main translation step for one outgoing provider request. It takes a request that looks like it is going to the provider, sends it through Composio's proxy endpoint instead, and returns a response shaped like the provider's original response.

**Data flow**: It starts with an `httpx.Request` containing a method, URL, headers, query parameters, timeout settings, and possibly a body. It reads the body, builds a JSON payload for Composio, filters out headers that should not be forwarded, and sends a new POST request to Composio using the API key and connected account id. It reads Composio's response with size protection, then either returns Composio's error response directly or reconstructs the provider response from Composio's JSON payload.

**Call relations**: This function is the front door of `ComposioProxyTransport`. During a proxied request, it calls `_read_bounded` so the broker response cannot grow without limit when a cap is set. If Composio succeeds, it hands the decoded response body to `_provider_response`, which turns Composio's wrapper format back into a normal HTTP response for the caller.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This function safely reads the full response body from Composio. When a maximum size is configured, it stops reading and raises an error if the response is too large, which protects the shared proxy process from buffering too much data.

**Data flow**: It receives an `httpx.Response` from Composio. If no size limit is set, it simply reads all bytes and returns them. If a limit is set, it reads the response piece by piece, adds each piece to a buffer, checks the total size, closes the response if the limit is exceeded, and raises a Composio error instead of returning oversized data.

**Call relations**: It is called by `ComposioProxyTransport.handle_async_request` right after the Composio proxy call returns. Its job is to make sure the next step, response reconstruction, only receives a body that is safe to keep in memory.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This function turns Composio's proxy result into the HTTP response the original caller expected from the provider. It unwraps Composio's nested response format, restores provider status and headers, and handles both JSON/text bodies and binary file redirects.

**Data flow**: It receives a decoded JSON payload from Composio and the original request. It peels away nested `data` envelopes until it reaches the provider-like response, chooses a status code, copies safe headers, and builds response bytes from the returned `data`. If Composio reports binary data stored at a presigned URL, it returns a 302 redirect with a `location` header instead of pulling the large file through this process.

**Call relations**: It is called by `ComposioProxyTransport.handle_async_request` after the proxy response has been read and decoded. It is the final conversion step that lets the rest of the system keep treating the result like a normal provider HTTP response.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport used to talk to Composio. It is used when the proxy transport is finished so network resources are not left open.

**Data flow**: It takes no new request data. It calls close on the inner transport, which releases any connections or other transport resources it owns. Nothing is returned.

**Call relations**: It is the cleanup companion to requests made through `ComposioProxyTransport`. `ComposioRequestForwarder.forward` calls it in a `finally` step so cleanup happens even if the request fails or times out.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request from the broker side through Composio using a connected account. It is used for CLI-style forwarding where the system must execute a request with Composio-held credentials, while enforcing response size and total time limits.

**Data flow**: It receives an account id, HTTP method, target URL, headers, and raw body bytes. It gets the Composio client and API key, builds a `ComposioProxyTransport`, wraps the incoming data into an `httpx.Request`, and runs the proxy request under a wall-clock timeout. It reads the resulting response body, closes the transport, and returns a `ForwardedResponse` containing the status, headers, and bytes; if the whole operation takes too long, it raises a Composio timeout error.

**Call relations**: This is the higher-level entry used by the broker forwarding path. It constructs and uses `ComposioProxyTransport` for the actual request translation, then packages the result into `ForwardedResponse` so the caller gets a simple forwarded HTTP answer.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

Composio exposes tool search through an MCP endpoint. MCP, or Model Context Protocol, is a standard way for an app to talk to external tools. This file is the small bridge that opens that conversation, asks one tool question, and then closes the connection.

The main job is to call a named MCP tool, such as Composio’s semantic tool search tool, over streamable HTTP. Streamable HTTP is just an HTTP connection style that can carry MCP messages back and forth. The file uses FastMCP’s client library to do the connection work, so the rest of the project does not need to know the details of the MCP wire protocol.

After the tool replies, the file normalizes the result. Some MCP responses already include parsed dictionary-like data. If so, it returns that directly. If not, it checks for structured content. If that is also unavailable, it looks for a text block and tries to parse it as JSON. As a last resort, it wraps whatever it found in a simple dictionary. This matters because callers can depend on getting a dictionary back instead of having to understand every possible MCP response shape.

A small testing detail is important: the FastMCP Client is imported as a module-level name, so tests can replace it with a fake client and avoid contacting a real Composio endpoint.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: This function opens a temporary MCP connection to a given endpoint, calls one named tool with the supplied arguments, and returns the result as a plain dictionary. Someone would use it when they need a single Composio Tool Router search call without exposing the rest of the code to MCP response details.

**Data flow**: It receives an endpoint URL, a tool name, a dictionary of arguments, HTTP headers, and a timeout. It builds a streamable HTTP transport with the endpoint and headers, opens a FastMCP client, sends the tool call, and waits up to the timeout. When the reply comes back, it first returns parsed dictionary data if present, then dictionary-style structured content if present, then tries to read the first text response as JSON. If the text is not JSON, or if the response is in another shape, it wraps the value in a simple dictionary so the caller still gets a predictable result.

**Call relations**: Higher-level Composio search code calls this when it needs to ask the Tool Router one question. Inside, it hands connection setup to FastMCP’s Client and StreamableHttpTransport, then uses JSON parsing only if the MCP response arrived as plain text. It does not execute tools itself; it only performs the search-side MCP call and gives the cleaned-up result back to its caller.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `request handling during connector OAuth consent`

This file solves a mismatch between two systems. ufo expects an OAuth provider to give it a ready-to-use authorization URL right away, like handing someone a printed ticket. Composio, however, has to create that consent link through an asynchronous API call, more like calling the ticket desk first. To bridge that gap, this file makes ufo send the browser to a local extension route first.

The flow starts with `ComposioOAuthProvider.authorize_url`, which builds a URL pointing to this extension's `/ext/composio/oauth` route. That route is `oauth_route`. On the first visit, it asks Composio to create a consent link for the requested provider and redirects the browser there. Composio then shows the user its hosted consent screen.

When the user finishes, Composio redirects back to the same route with a `connected_account_id`. The route then redirects to ufo's real callback and passes that account id as the OAuth `code`. Later, `ComposioOAuthProvider.exchange` checks with Composio that this account belongs to the expected workspace user and provider before returning an `OAuthAccount` to ufo.

An important safety detail is that this file does not store provider tokens. The secret stays inside Composio, and ufo only binds to the connected account id after Composio confirms ownership.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL that ufo gives to the user's browser when a Composio-backed connector needs consent. Instead of pointing straight to Composio, it points to this extension's bridge route so the async Composio link can be created there.

**Data flow**: It receives the sealed OAuth `state` value and ufo's final `redirect_uri`. It packages the provider name, state, and callback into query parameters, extracts the origin from the callback URL, and returns a bridge URL under `/ext/composio/oauth`. Nothing external is changed at this step.

**Call relations**: This is the first step of the connect flow for this provider. It relies on `_origin` to reuse the callback's scheme and host, then the user's browser later calls `oauth_route`, which continues the flow by asking Composio for the real consent link.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–53)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This turns the account id returned from Composio into an `OAuthAccount` that ufo can bind to the workspace. It also verifies that the account id belongs to the expected workspace-scoped Composio user and matches the intended provider.

**Data flow**: It receives the `code`, which in this flow is really Composio's connected account id, plus the workspace id. It builds the expected Composio external user id from that workspace id, asks the Composio client to look up and validate the connected account, and returns the resulting OAuth account record. It does not read or store provider access tokens.

**Call relations**: This runs after `oauth_route` redirects back to ufo's normal callback with the connected account id as the code. It hands the validation work to the shared Composio client, which confirms the account before ufo accepts the grant.

*Call graph*: 1 external calls (composio_client).


##### `oauth_route`  (lines 56–93)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the HTTP bridge route used for both halves of the Composio consent trip. It starts the trip by creating a Composio consent link, and it finishes the trip by forwarding Composio's connected account id back into ufo's standard callback flow.

**Data flow**: It reads query parameters from the incoming browser request. If `state` or `callback` is missing, it returns a bad request response. If Composio has returned a `connected_account_id`, it redirects the browser to the callback with that id as the OAuth `code`. If Composio returned a failure `status` without an account id, it returns an error message instead of silently restarting consent. If this is the start of the flow, it reads the requested provider, builds a return URL back to itself, asks Composio for a connect link tied to the current workspace, and redirects the browser to that link.

**Call relations**: The browser reaches this route after `ComposioOAuthProvider.authorize_url` sends it here. On the start leg, it calls `_origin` and the Composio client to create the hosted consent URL. On the return leg, it does not call Composio again; it redirects to core ufo's callback so `ComposioOAuthProvider.exchange` can validate and bind the connected account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 96–100)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts just the scheme and host from a full URL, for example turning `https://example.com/path` into `https://example.com`. It makes sure bridge URLs are built on a valid web origin.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a host name. If the URL is valid, it returns the origin. If it is missing those required parts, it raises an error instead of building an unsafe or broken redirect URL.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` call this helper when they need to build bridge URLs that live beside the final callback. It is the shared guardrail that prevents the OAuth bridge from using a callback URL without a real web address.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Pipedream bridge
Pipedream integration files connect hosted OAuth accounts, list and run catalog actions, and proxy provider calls through connected accounts.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Pipedream provides ready-made actions for many apps, such as sending an email or creating a record. UFO needs to treat those actions like ordinary connector tools, without every caller knowing Pipedream's own API details. This file is that translator.

The main class, PipedreamBroker, is deliberately stateless. Each method asks for a fresh Pipedream client when it runs. That matters because tests or deployments may swap the network transport underneath, and this design avoids holding on to stale connections.

The broker can search Pipedream's action catalog, turn an action definition into a simple input schema, run an action, and return credentials for proxying requests through Pipedream. When it builds a schema, it hides Pipedream's internal fields, especially the app account field. That field is filled in by the broker at execution time using the granted account, like a clerk adding the official account stamp before mailing a form.

The file also turns Pipedream-specific problems into clearer UFO-level errors. If an action key is unknown, it tries to show the valid action keys. If an account grant is stale, it adds guidance telling the user to reconnect. File handling is one-way: Pipedream actions accept file URLs as inputs, and any output files are read from Pipedream's file stash URLs.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–67)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for one provider and returns them as UFO broker tools. It also protects users from Pipedream's strict search behavior by falling back to the app's top actions when a non-empty search returns nothing.

**Data flow**: It receives a workspace id, a provider name, and a search query. It looks up the provider's Pipedream app name, asks the Pipedream client for matching actions, converts the returned catalog entries into BrokerTool objects, and returns them as a tuple. If the query found no actions, it repeats the lookup with an empty query.

**Call relations**: This is the catalog lookup used directly by search. It relies on _spec to translate UFO's provider name into Pipedream's app slug, gets a per-call Pipedream client, and hands the raw Pipedream list response to _listed_tools so callers receive clean broker tool objects instead of Pipedream API records.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 69–75)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one Pipedream action, so an agent knows what arguments it may supply. It hides account-binding and other Pipedream-internal fields that the caller should not fill in.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts the configurable properties, turns those properties into a JSON-style input schema, and returns a BrokerTool containing the slug, description, and schema.

**Call relations**: When a caller needs details for a specific action, this method calls _definition to fetch the Pipedream action metadata, _props to pull out the configurable inputs, _input_schema to make them readable to UFO, and _str to safely use the description.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 77–112)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It checks that the account belongs to the expected app, binds that account into the action's hidden app field, sends the run request, and turns important failures into clearer errors.

**Data flow**: It receives workspace and provider information, an action slug, user-supplied arguments, an account id, and an optional idempotency key. It fetches the action definition, copies the arguments, inserts the account binding into the correct app slot, verifies the account belongs to this workspace and app, and calls Pipedream's run API. It returns the action response, unless Pipedream reports an action error or a stale account, in which case it raises a PipedreamError with useful context.

**Call relations**: This is the main execution path. It uses _definition to understand the action, _app_slot to find where the account must be inserted, _spec to confirm the provider's Pipedream app, and _key_miss to improve unknown-action errors. When Pipedream errors mention a missing external user or account, it asks _stale_account and _reconnect_error to turn that into reconnect guidance for the agent.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 114–132)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Pipedream action and presents them as downloadable broker files. This lets the sandbox fetch output files from Pipedream's file stash instead of needing direct access to Pipedream's temporary runtime filesystem.

**Data flow**: It receives a Pipedream action response. It looks inside the response exports for file-stash upload records, skips malformed entries, takes each valid download URL, derives a friendly file name from the reported local path, and returns BrokerFile objects.

**Call relations**: This runs after execute returns a response. It does not call Pipedream again; it only interprets the response shape and creates BrokerFile records that the broader connector system can hand to the sandbox or caller.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 134–146)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged file uploads for Pipedream actions. Pipedream expects file inputs as ordinary URLs, so UFO should share a workspace file and pass its download link instead.

**Data flow**: It receives details for a would-be upload, such as filename, MIME type, and checksum. Instead of preparing an upload location, it immediately raises a ValueError explaining the correct file-input path.

**Call relations**: This is part of the broker interface, but Pipedream cannot use this upload style. Callers that try to stage a file for a Pipedream action are stopped here and told to use share_file so the action receives a URL.


##### `PipedreamBroker.search`  (lines 148–149)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a search result for Pipedream actions. Since Pipedream does not provide a separate planning or routing layer here, the result is simply the matching tools.

**Data flow**: It receives a workspace id, provider name, and query. It delegates the actual lookup to PipedreamBroker.tools, wraps the returned tools in a BrokerSearch object, and returns that object.

**Call relations**: This is a thin wrapper around tools. When the wider connector system asks to search a provider, this method reuses the same catalog lookup path and packages the answer in the standard search-result type.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 151–172)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can proxy requests through Pipedream for a connected account. It verifies that the account belongs to the requested workspace and app before returning a transport that can use it.

**Data flow**: It receives a workspace id, provider name, and account id. It looks up the provider spec, fetches the connected account from Pipedream, turns a missing account into reconnect guidance, checks that the account authenticates the expected app, and returns a Credential containing a PipedreamProxyTransport.

**Call relations**: This is used when the system needs a live authenticated transport rather than running a catalog action. It calls _spec to identify the expected Pipedream app and _reconnect_error if the account is missing. It builds PipedreamProxyTransport with the Pipedream client, account id, external user id, and an HTTP transport.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 174–182)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for an action slug. If Pipedream says the action does not exist, it converts that into UFO's UnknownBrokerTool signal.

**Data flow**: It receives an action slug. It asks the current Pipedream client for the action definition, treats a 404 response as an unknown tool, and returns the definition data as a dictionary. If the response has a nested data object, it returns that inner object; otherwise it returns the payload itself.

**Call relations**: Both schema and execute need action definitions before they can continue. This helper centralizes the Pipedream lookup and the not-found translation so those higher-level methods can work with a normal dictionary or handle UnknownBrokerTool.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 184–198)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when execution refers to an action key that Pipedream does not know. Instead of only saying 'not found,' it tries to include the real action keys available for that app.

**Data flow**: It receives a Pipedream client, provider name, and missing action slug. It looks up the provider's Pipedream app, tries to list that app's actions, converts them to broker tools, and returns a PipedreamError whose message includes the valid slugs. If listing actions fails, it returns a simpler not-found error.

**Call relations**: execute calls this after _definition reports an unknown action. It uses _spec to find the app and _listed_tools to clean up the catalog response, then hands back a PipedreamError that can guide the model's next attempt.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 201–208)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Detects whether a Pipedream error looks like it was caused by an old or missing connected account grant. The check is intentionally narrow so unrelated provider errors are not mistaken for reconnect problems.

**Data flow**: It receives a PipedreamError and the account id being used. It lowercases the error body and checks for Pipedream's 'external user not found' wording or for the specific account id together with 'not found'. It returns true if the error matches those stale-account patterns, otherwise false.

**Call relations**: execute uses this after Pipedream run failures and action-level errors. If it returns true, execute passes the error to _reconnect_error so the user gets advice to reconnect the provider account.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 211–212)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds user-facing reconnect guidance to a Pipedream error. This helps an agent recover when an account grant has gone stale or no longer belongs to the current broker state.

**Data flow**: It receives an existing PipedreamError and a provider name. It keeps the original status code, appends standard stale-grant guidance for that provider to the error body, and returns a new PipedreamError.

**Call relations**: execute calls this when a run failure appears to involve a stale account. credential also calls it when a requested connected account cannot be found. In both cases it turns a raw Pipedream failure into an instruction the user or agent can act on.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 215–219)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the Pipedream connector specification for a UFO provider name. This is how the broker translates from UFO's provider label to Pipedream's app slug.

**Data flow**: It receives a provider string. It checks the Pipedream connector registry, returns the matching ConnectorSpec when present, and raises a KeyError if no provider is registered.

**Call relations**: tools, execute, credential, and _key_miss all need this lookup before talking about a provider with Pipedream. It is the shared gatekeeper that prevents unknown provider names from silently becoming bad Pipedream requests.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 222–234)

```
def _listed_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Turns Pipedream's action-list response into UFO BrokerTool objects. It keeps only usable action keys and short descriptions.

**Data flow**: It receives a dictionary returned by Pipedream's list-actions API. It reads the data list, skips anything malformed or missing a key, converts each valid item into a BrokerTool with a slug and description, and returns the tools as a tuple.

**Call relations**: tools uses this to return clean search results to callers. _key_miss also uses it when building a helpful not-found message that lists available action keys.

*Call graph*: calls 1 internal fn (_str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 237–239)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable properties from a Pipedream action definition. These properties are the raw ingredients for both input schemas and account binding.

**Data flow**: It receives an action definition dictionary. It reads the configurable_props value, keeps only entries that are dictionaries, and returns them as a list. If the field is missing or not a list, it returns an empty list.

**Call relations**: schema calls this before building the caller-facing input schema. _app_slot also calls it when searching for the special Pipedream app field where the connected account must be inserted.

*Call graph*: called by 2 (schema, _app_slot).


##### `_app_slot`  (lines 242–249)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the hidden action field where Pipedream expects the connected app account. Without this field, the broker cannot safely run the action for a granted account.

**Data flow**: It receives an action definition and its slug. It scans the configurable properties for one whose type is Pipedream's app-property type and whose name is a valid string. It returns that property name, or raises a PipedreamError if no app slot exists.

**Call relations**: execute calls this right before running an action. It uses _props to inspect the definition and returns the field name that execute fills with the account's authProvisionId.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 252–275)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the JSON-style input schema shown to the agent for a Pipedream action. It includes only fields the agent should provide and hides internal Pipedream fields such as the account slot and service-only properties.

**Data flow**: It receives a list of Pipedream configurable-property dictionaries. For each usable property, it chooses a JSON type, adds a description when available, records whether the field is required, and skips internal property types. It returns a schema dictionary with object properties and, when needed, a required list.

**Call relations**: schema calls this after _props extracts the action inputs. It uses _str to safely read property types, then hands schema back to schema so the final BrokerTool can describe what arguments the action accepts.

*Call graph*: calls 1 internal fn (_str); called by 1 (schema).


##### `_str`  (lines 278–279)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. It prevents non-string values from leaking into descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: schema uses this for action descriptions, _listed_tools uses it for listed-action descriptions, and _input_schema uses it when reading Pipedream property types. It is a small guardrail around data received from outside APIs.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during account connection`

ufo needs a normal OAuth-style flow: send the user to an authorization web page, then receive a callback with a code that can be exchanged for an account. Pipedream works a little differently. Before the user can be sent to Pipedream, the server must first ask Pipedream for a temporary Connect token, and that request is asynchronous. This file solves that mismatch by making ufo's authorization URL point back to this extension first.

The flow is like a receptionist routing a visitor. First, `PipedreamOAuthProvider.authorize_url` sends the browser to this extension's `/ext/pipedream/oauth` route, carrying the provider name, the protected state value, and the final callback URL. Then `oauth_route` asks Pipedream for a Connect token tied to this exact workspace and state, builds the hosted Pipedream Connect Link, and redirects the browser there. When Pipedream sends the browser back, the same route checks whether consent succeeded. If it did, it finds the newest connected Pipedream account for that exact temporary user and sends its account id back to ufo core as the code.

Finally, `PipedreamOAuthProvider.exchange` re-fetches that exact account and verifies it belongs to the expected Pipedream app before ufo binds the grant. This matters because overlapping connect flows must not accidentally claim each other's accounts.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first URL the user's browser should visit when starting a Pipedream-backed connection. Instead of sending the user straight to Pipedream, it sends them to this extension's bridge route so the server can create the needed Pipedream Connect token first.

**Data flow**: It receives ufo's protected `state` value and the final `redirect_uri`. It extracts the web origin, such as `https://example.com`, adds the provider, state, and callback as query text, and returns a complete bridge URL under `/ext/pipedream/oauth`. It does not contact Pipedream itself.

**Call relations**: This is called by ufo's connect machinery when it needs an authorization page. It relies on `_origin` to make sure the callback URL has a usable scheme and host, then hands the browser off to `oauth_route`, which performs the asynchronous Pipedream work.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–63)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the account id returned from the bridge route into the OAuth account record ufo core expects. It also double-checks that the account really belongs to this provider's Pipedream app before allowing the connection to be bound.

**Data flow**: It receives a `code`, which in this flow is the Pipedream account id, plus the workspace id and protected state. It recreates the state-specific Pipedream external user id, asks Pipedream for that exact connected account, checks the app name, and returns an `OAuthAccount` containing the account id. If the account belongs to a different app, it raises a Pipedream error instead of returning a grant.

**Call relations**: This runs after `oauth_route` redirects back to ufo core with a successful code. It calls into the Pipedream client to fetch and verify the account, then gives ufo core the small account object it needs to finish saving the connection.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 66–109)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge for both halves of the Pipedream connect flow. It starts consent by creating a Pipedream Connect token, and later receives Pipedream's success or failure redirect.

**Data flow**: It reads query parameters from the incoming HTTP request: provider, state, callback, and sometimes an outcome marker. If required information is missing, it returns an error response. If the provider is unknown, it returns not found. On a successful return from Pipedream, it finds the newest account for this workspace-and-state-specific user, then redirects to ufo core with the state and account id. On a failed return, it sends a clear failure response. On the initial visit, it creates a Pipedream Connect token with success and error redirects pointing back to this same route, builds the Pipedream hosted link for the chosen app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: The browser reaches this route after `PipedreamOAuthProvider.authorize_url` points it here. During the start leg, it calls Pipedream to create the Connect token and sends the browser onward to Pipedream. During the return leg, it calls Pipedream again to identify the connected account, then hands control back to ufo core, which later calls `PipedreamOAuthProvider.exchange`.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 112–116)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the scheme and host from a full URL, such as turning `https://example.com/path` into `https://example.com`. This keeps redirects anchored to the same web origin as the callback.

**Data flow**: It receives a URL string, parses it, and checks that it has either `http` or `https` plus a host name. If the URL is valid, it returns just the origin. If it is missing those required parts, it raises an error because the OAuth bridge would not know where to send the browser safely.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` use this helper before constructing bridge URLs. It is the small safety gate that prevents those functions from building redirects from incomplete or non-web callback addresses.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector auth and action request handling`

This file is the bridge between the project and Pipedream’s hosted connector system. Think of Pipedream as a locked credential vault plus a remote control panel: users approve access in Pipedream, Pipedream keeps the real provider token, and this project stores only a connected-account id. That matters because it reduces secret handling and prevents the app from accidentally leaking a user’s Gmail or other provider credentials.

The file defines which Pipedream-backed connectors this extension is allowed to use, currently Gmail. It also defines small data objects for connect tokens and connected accounts, plus a custom error type so failures are loud and clear.

The main piece is `PipedreamClient`. It first authenticates this deployment to Pipedream using client credentials from the environment, then reuses that access token until it is close to expiring. With that token, it can mint a hosted consent link, read connected accounts, search Pipedream’s action catalog, fetch action details, and run an action on Pipedream’s servers.

A key safety feature is ownership checking. A project-level Pipedream token can read many accounts, so this client verifies that an account’s external owner id matches the expected user or workspace before allowing it to be used. Without this guard, one user or workspace could accidentally act through another user’s connected account.

#### Function details

##### `PipedreamError.__init__`  (lines 79–82)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception for problems reported by Pipedream or for replies that do not contain the information this project needs. It keeps both the numeric status and the response body so callers can report or diagnose the failure.

**Data flow**: It receives a status number and a text body, turns them into a message like `pipedream 403: ...`, and stores the original pieces on the error object. The result is an exception that can be raised instead of quietly continuing with bad or missing data.

**Call relations**: This error is raised throughout the Pipedream broker and client whenever something is unsafe or unusable: failed API replies, missing tokens, missing account owners, unhealthy accounts, or rejected ownership checks. It is the common stop sign used before the system would otherwise trust a bad connector state.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 120–141)

```
async def access_token(self) -> str
```

**Purpose**: Gets the access token that lets this deployment call Pipedream’s API. It caches the token for the process so normal calls do not need to ask Pipedream for a fresh token every time.

**Data flow**: It first looks in the in-memory token cache using the client id. If a still-valid token is present, it returns it. Otherwise it opens an HTTP client, sends the client id and secret to Pipedream’s OAuth token endpoint, checks the response body, stores the new token with its expiry time, and returns the token string.

**Call relations**: The private request helpers `_get` and `_post` call this before making authenticated Pipedream API requests. It uses `_http` to create the temporary HTTP client and `_body` to turn the HTTP response into a safe dictionary or raise `PipedreamError`.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 143–158)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and browser link for a user to approve access to an outside app. This is the start of the hosted consent flow.

**Data flow**: It receives an external user id plus success and error redirect URLs. It sends those to Pipedream, expects back a token and a connect-link URL, validates that both are present strings, and returns them as a `ConnectToken` object.

**Call relations**: Higher-level OAuth or broker code uses this when it needs to send a user to Pipedream’s hosted consent page. Internally it hands the POST request to `_post`; if Pipedream’s answer is missing the token or link, it raises `PipedreamError` instead of returning an unusable link.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 160–167)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and verifies that it belongs to the exact external user expected. This prevents the project-level Pipedream credential from being used as a shortcut to someone else’s account.

**Data flow**: It receives a Pipedream account id and an expected external user id. It fetches the account record, unwraps the `data` field if Pipedream used one, and passes the record through `_owned_account`, which checks owner and health. It returns a `ConnectedAccount` if everything matches.

**Call relations**: This is used after or around a consent flow when code needs to trust a specific account id. It relies on `_get` for the API read, `_dict` for safe shape handling, and `_owned_account` for the ownership guard.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.workspace_account`  (lines 169–179)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads one connected account and verifies that the account was connected under the given workspace. It allows execution only when the account owner id follows this project’s workspace ownership format.

**Data flow**: It receives an account id and workspace id. It fetches the account record, converts it into a `ConnectedAccount`, then checks whether the account’s external user id belongs to that workspace. If the check passes it returns the account; otherwise it raises a forbidden `PipedreamError`.

**Call relations**: This is the workspace-level safety gate before using a saved Pipedream account for work. It uses `_get` to read from Pipedream, `_account` to validate the account record, and `_workspace_owns_external_user` to decide whether the workspace is allowed to use it.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 181–195)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for a particular external user and app. This is useful right after a user finishes consent, when the system needs to discover which Pipedream account was just created or updated.

**Data flow**: It receives an external user id and app slug. It asks Pipedream for matching accounts, keeps only dictionary-shaped records, chooses the one with the newest `created_at` value, checks that it has an id, and then verifies ownership before returning a `ConnectedAccount`.

**Call relations**: Consent-completion code can use this to correlate Pipedream’s return flow back to the user who started it. It builds on `_get` for the account list, `_dict` for safe record handling, and `_owned_account` for the final ownership check.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 197–203)

```
async def list_actions(self, app: str, query: str='', limit: int=ACTION_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Searches Pipedream’s catalog of ready-made actions for a given app, such as Gmail actions. This lets the dynamic tool layer discover what operations are available.

**Data flow**: It receives an app slug, an optional search phrase, and a result limit. It turns those into query parameters, sends a GET request to Pipedream, and returns the response dictionary from the action catalog.

**Call relations**: The Pipedream broker calls this when it needs to resolve or suggest an action key. The method delegates the authenticated HTTP work to `_get` and simply shapes the search parameters.

*Call graph*: calls 1 internal fn (_get); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 205–206)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition of one Pipedream action component. That definition tells the rest of the system what inputs the action expects and how it is described.

**Data flow**: It receives an action key, requests the matching component from Pipedream, and returns the response dictionary. It does not interpret the component itself; it provides the raw definition to the caller.

**Call relations**: This is used when higher-level connector code needs more than a search result and wants the full action description. It relies on `_get` for authentication, request sending, and response parsing.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 208–226)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on Pipedream’s servers for a particular external user. It also requests a fresh file stash so files produced by the action can be returned as downloadable links instead of unreadable temporary paths.

**Data flow**: It receives an action key, external user id, and configured input values. It builds the run request, adds `stash_id` set to `NEW`, checks that the JSON payload is not larger than the configured one-megabyte limit, then posts the request and returns Pipedream’s response dictionary.

**Call relations**: The broker uses this when an approved connector tool is actually executed. The method hands the HTTP call to `_post`; before that, it performs the payload-size check so oversized tool arguments are rejected locally.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 228–231)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends an authenticated GET request to Pipedream and returns a parsed dictionary response. It is the shared helper for read-style API calls.

**Data flow**: It receives an API path and optional query parameters. It gets an access token, opens an HTTP client with that token, sends the GET request, and passes the response to `_body`. The output is a dictionary or an exception if the response is not usable.

**Call relations**: Public methods such as account lookup, action search, and action definition all pass through this helper. It centralizes the repeated pattern of getting a token, creating an authenticated client, and parsing the response.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 5 (action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 233–236)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Sends an authenticated POST request to Pipedream and returns a parsed dictionary response. It is the shared helper for write or command-style API calls.

**Data flow**: It receives an API path and a JSON-ready body dictionary. It gets an access token, opens an HTTP client with that token, posts the body as JSON, and converts the response through `_body`.

**Call relations**: `connect_token` and `run_action` use this helper because both create something or ask Pipedream to perform an action. Like `_get`, it keeps token use and response checking in one place.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 238–251)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates a short-lived asynchronous HTTP client configured for Pipedream. For authenticated calls, it adds the bearer token and environment header; for the token-minting call, it leaves those headers off.

**Data flow**: It receives an optional token. If a token is present, it builds headers containing authorization and the Pipedream environment. It returns an `httpx.AsyncClient` with the Pipedream base URL, timeout, headers, and optional test transport.

**Call relations**: Every network call in this client goes through this factory. `access_token` uses it without a token, while `_get` and `_post` use it with a token after authentication has already happened.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 254–255)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This avoids crashes or accidental trust when Pipedream returns a field in an unexpected shape.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Account-reading code uses this whenever it unwraps Pipedream response fields such as `data` or nested `app`. It is a small guardrail used before deeper validation happens in `_account` or `_owned_account`.

*Call graph*: called by 4 (connected_account, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 258–271)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that a Pipedream account record is healthy and belongs to the expected external user. It is the direct user-level ownership guard.

**Data flow**: It receives an account record, account id, and expected external user id. It first converts the record into a `ConnectedAccount` using `_account`, then compares the account’s owner to the expected owner. If they match it returns the account; if not it raises `PipedreamError`.

**Call relations**: `connected_account` and `newest_account` call this after fetching account data from Pipedream. It builds on `_account` for basic record validation, then adds the stronger owner check needed to prevent cross-user use.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 274–286)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into the project’s simple `ConnectedAccount` object, while rejecting records that are missing an owner or marked unhealthy. It is the basic account validation step.

**Data flow**: It receives a raw account record and account id. It reads the external owner id, checks that it is a non-empty string, rejects accounts marked unhealthy, reads the app slug if present, and returns a `ConnectedAccount` containing the account id, app, and owner id.

**Call relations**: `workspace_account` uses this before checking workspace ownership, and `_owned_account` uses it before checking exact user ownership. It calls `_dict` to safely inspect the nested app field and raises `PipedreamError` when the account record cannot be trusted.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 289–290)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used for Pipedream external user ids that belong to a workspace. This gives all workspace-scoped connector users a recognizable naming pattern.

**Data flow**: It receives a workspace UUID and returns a string made from the project prefix, the workspace id in compact hexadecimal form, and a trailing underscore. Nothing outside the returned string is changed.

**Call relations**: `connection_user_id` uses this when creating a new state-specific external user id, and `_workspace_owns_external_user` uses it when checking whether an existing external user id belongs to a workspace.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 293–302)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user id belongs to a given workspace. This is a safety check before letting workspace code use a connected account.

**Data flow**: It receives a workspace id and an external user id. It accepts an older simple workspace id format, or checks for the newer workspace prefix followed by a 32-character lowercase hexadecimal connection id. It returns true only when the format proves the id belongs to that workspace.

**Call relations**: `workspace_account` calls this after reading an account from Pipedream. The helper uses `workspace_user_prefix` so the creation and checking rules stay consistent.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 305–307)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for a particular workspace and connection state. It lets the consent flow tie a returned account back to the exact state that started it without storing a secret token.

**Data flow**: It receives a workspace id and state string. It hashes the state, takes the first 32 hexadecimal characters as a connection id, prefixes it with the workspace user prefix, and returns the final external user id string.

**Call relations**: OAuth setup code can use this before calling `connect_token`, so Pipedream records the account under a predictable external owner. Its format is later recognized by `_workspace_owns_external_user` during workspace account checks.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 310–318)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Converts an HTTP response from Pipedream into a safe dictionary. It raises a `PipedreamError` for HTTP failures or for successful replies that are not JSON objects.

**Data flow**: It receives an HTTP response. If the status code shows an error, it raises with the status and response text. If the response has no content, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is a dictionary.

**Call relations**: `access_token`, `_get`, and `_post` all use this immediately after receiving a Pipedream response. This keeps response validation consistent across token minting, reads, and action execution.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 321–339)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds the deploy’s real `PipedreamClient` from environment variables. It fails early if the required Pipedream client id, client secret, or project id is missing.

**Data flow**: It reads the required Pipedream settings from the process environment, plus an optional environment name with a production default. If any required value is absent, it raises a runtime error. Otherwise it returns a configured `PipedreamClient`.

**Call relations**: Higher-level connector routes and broker code use this when they need the live Pipedream client. It is the file’s configuration doorway: everything else assumes the returned client has enough information to authenticate to Pipedream.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling`

Many connected services, like Gmail or Slack, require a private access token. In this setup, Pipedream keeps that token hidden and injects it on the server side. This file provides the bridge that makes that possible. Think of it like sending a sealed letter through a trusted receptionist: the app writes where the letter should go, Pipedream adds the private key needed to enter, and the final reply comes back unchanged.

The main piece is `PipedreamProxyTransport`, an HTTP transport for `httpx`, which is a Python library for making web requests. A transport is the lower-level part that actually sends the request. Instead of sending the request straight to the provider, this transport rewrites it into a Pipedream Connect Proxy call. It base64-url-encodes the original provider URL so it can safely fit inside the proxy path, adds the account and user identifiers as query parameters, and adds a fresh Pipedream authorization token.

It also carefully rewrites request headers. Pipedream only forwards headers that start with `x-pd-proxy-`, so ordinary useful headers are given that prefix, while transport-level headers such as `host`, `content-length`, and `authorization` are deliberately dropped. The response from Pipedream is returned directly, preserving the original provider status code, body, and headers. That matters because connector code may rely on exact responses, such as a 404 meaning a cursor has expired.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This function takes a normal outgoing provider request and sends it through Pipedream's proxy instead. It exists so the connector can behave as if it is calling the provider directly, while Pipedream secretly adds the real account credential.

**Data flow**: It receives an `httpx.Request` aimed at the provider. It first asks the Pipedream client for an access token, reads the request body, filters and re-prefixes safe request headers, and encodes the original provider URL into a proxy-safe string. It then builds a new request to Pipedream's proxy endpoint with the user and account identifiers attached, sends that new request through the inner transport, and returns the response it gets back.

**Call relations**: When `httpx` is about to send a request using this transport, it calls this method. Inside the method, the original request is not sent directly; it is repackaged as a Pipedream proxy request using `httpx.URL` and `httpx.Request`, with the original URL encoded by `base64.urlsafe_b64encode`. The finished proxy request is handed to the wrapped inner transport, which performs the actual network work.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This function closes the underlying transport when the proxy transport is no longer needed. It helps release network resources cleanly, such as open connections.

**Data flow**: It takes no new input beyond the transport object itself. It forwards the close request to the inner transport, and the result is that the lower-level network transport gets a chance to shut down properly.

**Call relations**: When the surrounding HTTP client is being closed, it calls this method. This proxy transport does not own separate cleanup work of its own, so it simply passes the close operation down to the inner transport that actually holds the network resources.


### GitHub App credentials
GitHub App files validate workspace installation ownership and exchange sealed installation records for short-lived GitHub access tokens.

### `extensions/coding/ufo_ext_coding/connect.py`

`orchestration` · `GitHub connection setup and browser callback`

This file protects the “connect GitHub” setup flow. A GitHub App installation has an ID, but that ID alone is not proof that the workspace is allowed to use it. So the file uses a safer two-step check, like asking both for a ticket and then checking the ticket against the guest list.

First, `connect_github` gives an admin a GitHub installation link. The link includes sealed state: a short protected value created by ufo that says which workspace and credential slot this setup belongs to. GitHub later sends the browser back to ufo with that state, an authorization code, and the claimed installation ID.

Then `github_installed` handles the return from GitHub. It does not trust the installation ID by itself. Instead, `GitHubInstallExchange.reaches` exchanges the authorization code for the GitHub member’s own temporary access token, then asks GitHub which ufo App installations that member can actually see. Only if the claimed installation appears in GitHub’s answer does ufo bind it to the workspace credential slot.

The result is that a workspace can only connect a GitHub App installation that the authorizing member truly has access to. Without this file, someone with a valid setup link could try to attach another organization’s installation ID.

#### Function details

##### `connect_github`  (lines 48–72)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: Starts the GitHub connection process for a workspace admin. It creates a one-purpose installation link that sends the admin to GitHub and carries protected state so the later browser return can be tied back to the right workspace.

**Data flow**: It receives the current tool context and a short user-facing description. It checks that the speaker is a workspace admin, checks that this deployment has a GitHub App configured, asks the credential system to create sealed authorization state for the GitHub installation slot, and returns a text message containing the GitHub install URL with that sealed state attached.

**Call relations**: This is the user-facing start of the flow. It calls the tool context to confirm admin permission and to begin credential authorization, asks the manifest for the GitHub App ID, then packages the link in `TextContent` and `ToolResult` so the agent can show it to the admin.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 75–80)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: Finds which workspace a GitHub return request belongs to by reading the sealed state in the request. It is a safety check: only state created by ufo for this exact credential slot and purpose can identify a workspace.

**Data flow**: It receives an HTTP request from the callback route. It reads the `state` query parameter, passes it to the credential helper together with the expected slot and payload name, and gets back either the workspace ID or `None` if the state is missing, expired, wrong, or not genuine.

**Call relations**: This function is a small bridge between the browser redirect and the workspace credential system. It delegates the actual seal-checking to `authorized_slot_workspace`, so callers can treat the returned workspace ID as coming from ufo’s own protected state rather than from GitHub or the user.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 99–126)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: Checks whether the GitHub user who just authorized the flow can actually access the installation ID being claimed. This is the core anti-spoofing check for the connection process.

**Data flow**: It receives a GitHub authorization code and an installation ID. It sends the code, app client ID, and app secret to GitHub to get the member’s access token. If GitHub does not return a token, it raises `GitHubAuthorizationError`. With the token, it asks GitHub for the member’s visible installations, filters that list to installations of this ufo GitHub App, and returns `true` only if the claimed installation ID is in that list.

**Call relations**: This method is used during the callback handled by `github_installed`. It talks to GitHub through `httpx.AsyncClient`, and its yes-or-no answer decides whether the workspace credential will be bound or rejected.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 129–140)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: Builds the object that knows how to verify a GitHub installation against GitHub. It gathers the deployment’s GitHub App identity from configuration and environment variables.

**Data flow**: It reads the configured GitHub App ID from the manifest and the GitHub OAuth client ID and secret from environment variables. If no GitHub App is configured, it raises an error. Otherwise, it returns a `GitHubInstallExchange` loaded with those values.

**Call relations**: This is called by `github_installed` right before checking the GitHub callback. It keeps configuration lookup separate from the verification logic, and creates the `GitHubInstallExchange` that will perform the GitHub API calls.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 143–172)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Completes the GitHub installation callback after GitHub redirects the admin back to ufo. It verifies the authorization and, only if GitHub confirms access, saves the installation ID into the workspace credential slot.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error page. Otherwise, it creates an install exchange and asks whether the authorizing member reaches that installation. If GitHub rejects the code, it returns a failure page. If the member does not reach the installation, it returns a forbidden page. If the check succeeds, it binds the installation ID into the GitHub installation credential slot and returns a success page.

**Call relations**: This is the main return path for the setup flow started by `connect_github`. It calls `install_exchange` to get the verifier, uses `GitHubInstallExchange.reaches` through that object, asks the credential system to save the approved installation, and uses `_page` to turn each outcome into a simple browser response.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 175–183)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: Creates a simple HTML response page for the browser after the GitHub callback. It gives the user a clear success or failure message with the right HTTP status code.

**Data flow**: It receives a message and a status code. It wraps the message in a small HTML document and returns a `Response` with `text/html` media type and the given status.

**Call relations**: This helper is called by `github_installed` for every visible outcome: missing authorization, GitHub rejection, forbidden installation, or successful connection. It keeps the callback logic focused on decisions while this function formats the browser page.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup during git access or rule derivation`

This file solves a careful authentication problem. A workspace may have installed this product’s GitHub App, but the workspace should not store the App’s private key or a plain installation ID that someone could fake. Instead, it stores a sealed value: a protected package that proves “this workspace is bound to this installation.” When Git access is needed, this file opens that seal, asks GitHub for a fresh installation token, and returns it.

The main class, GitHubAppTokens, is like a ticket desk. It checks whether the workspace has a valid sealed installation, creates a signed GitHub App message called a JWT (a short-lived proof of identity), exchanges that proof with GitHub for an installation access token, and caches the token until shortly before it expires. The cache avoids asking GitHub again and again during one conversation.

A key safety rule runs through the file: if a workspace has a bad or unreadable sealed installation value, the code fails rather than falling back to a member’s personal token. That prevents the system from silently authenticating as the wrong identity. If there is no installation at all, then it returns nothing so another normal credential path can answer instead.

#### Function details

##### `_segment`  (lines 45–46)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: Builds one encoded piece of a JWT, which is the signed message GitHub uses to recognize this App. It turns a small dictionary into compact JSON, then into URL-safe base64 text without padding.

**Data flow**: It receives a dictionary such as a JWT header or body. It serializes that dictionary into compact JSON bytes, base64-encodes those bytes in a web-safe form, removes trailing padding characters, and returns the encoded bytes.

**Call relations**: GitHubAppTokens._jwt calls this helper twice: once for the JWT header and once for the JWT body. Those encoded pieces are then joined and signed to make the App’s proof of identity.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 69–81)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a workspace has a valid GitHub App installation bound to it. It does not mint a token; it only answers whether the sealed installation value exists and can be opened.

**Data flow**: It receives a workspace ID and a credential store. It asks the store for the installation slot. If the slot is unset, it returns false. If a value exists, it tries to open the sealed installation using the store’s encryption helper and the workspace information. If opening succeeds, it returns true; if opening fails, the error is allowed to surface.

**Call relations**: This method is used when the system needs to know whether the GitHub App path applies, for example before exporting credential behavior to the sandbox. It deliberately uses the same seal-opening check as GitHubAppTokens.secret, so a bad stored value does not produce one answer for setup and a different answer when credentials are actually used.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 83–107)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns the GitHub installation access token for a workspace, if that workspace has a valid App installation. If the workspace has no installation slot at all, it returns None so another credential source can be used.

**Data flow**: It receives a workspace ID and a credential store. It reads the sealed installation value from the store. If nothing is stored, it returns None. If something is stored, it opens the seal to get the installation identifier, builds a cache key from the workspace and installation, and checks whether a still-fresh token is already cached. If so, it returns that token. If not, it starts or reuses an in-progress minting task, waits for it safely, and returns the newly minted token.

**Call relations**: This is the main method other credential code calls when it needs a usable GitHub secret. It hands token creation to GitHubAppTokens._mint, but it also coordinates caching and makes sure two callers asking at the same time share one minting task instead of both contacting GitHub.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 109–117)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: Creates and records a fresh installation token for one workspace-installation pair. It also cleans up the “minting in progress” marker when the attempt finishes.

**Data flow**: It receives the cache key and the installation identifier. It calls GitHubAppTokens._installation_token to get a token and its expiry time from GitHub, stores that pair in the cache under the key, and returns it. Whether the call succeeds or fails, it removes its own pending task entry if it is still the current one.

**Call relations**: GitHubAppTokens.secret starts this method in an asynchronous task when no fresh cached token is available. This method is the bridge between the high-level cache logic in secret and the lower-level GitHub HTTP exchange in GitHubAppTokens._installation_token.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 119–151)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: Talks to GitHub to exchange this App’s signed proof for an installation access token. It raises a clear credential-minting error if GitHub cannot be reached, refuses the request, or replies with data that cannot be understood.

**Data flow**: It receives a GitHub installation identifier. It creates an asynchronous HTTP client, builds a request to GitHub’s installation access-token endpoint, and includes a fresh JWT from GitHubAppTokens._jwt in the Authorization header. If GitHub returns the expected success status, it reads the JSON response, extracts the token and expiry time, converts the expiry into a timestamp, and returns both. Network failures, non-success responses, and malformed responses become CredentialMintFailed errors.

**Call relations**: GitHubAppTokens._mint calls this when a new token is needed. This method calls GitHubAppTokens._jwt to prove the App’s identity before making the HTTP request to GitHub.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 3 external calls (__init__, fromisoformat, AsyncClient).


##### `GitHubAppTokens._jwt`  (lines 153–160)

```
def _jwt(self) -> str
```

**Purpose**: Creates the short-lived signed JWT that proves this deployed service owns the GitHub App registration. GitHub requires this proof before it will mint an installation token.

**Data flow**: It reads the current time, builds a JWT header and body containing the signing algorithm, issue time, expiry time, and App ID, and encodes those pieces with _segment. It signs the joined header and body with the App’s RSA private key using SHA-256, encodes the signature, joins all three pieces with dots, and returns the final text token.

**Call relations**: GitHubAppTokens._installation_token calls this immediately before contacting GitHub. The JWT it returns is not the final Git credential; it is a temporary proof used to ask GitHub for the real installation access token.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `app_tokens`  (lines 163–174)

```
def app_tokens(installation_slot: str) -> GitHubAppTokens
```

**Purpose**: Builds a GitHubAppTokens object from deployment environment variables. It makes startup fail loudly if the App ID or private key is missing or the key is not the expected RSA private key.

**Data flow**: It receives the name of the credential slot that stores sealed installation bindings. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the process environment, parses the private key from PEM text, verifies that it is an RSA private key, and returns a configured GitHubAppTokens instance.

**Call relations**: This is the setup helper used by surrounding code when wiring GitHub App credentials into the system. It prepares the GitHubAppTokens object that later answers bound and secret requests during credential lookup.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### Agent tool surfaces
Agent-facing tools provide safe connector search and execution plus guided Slack setup and read-only YC credential-backed actions.

### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `request handling`

A connector broker is like a front desk for many outside services. The agent cannot keep a fixed list of every possible GitHub or Slack action, so this file provides four general tools: search for available connectors, discover what a connector can do, search a connector by goal, and run one exact tool. When a tool is run, the code first checks the live connector registry for this turn, chooses the right broker, and asks which connected account should be used.

The most careful part is file and result handling. If an argument points to a workspace file, the file is hashed inside the sandbox, uploaded directly from the sandbox to the broker’s file store, and replaced with the broker’s own file reference. If the external tool creates files, they are downloaded directly into `/workspace/connector_files/`. This avoids piping large or sensitive file bytes through the serving process.

The file also cleans up bulky responses. If a provider returns base64, meaning bytes encoded as text, this code decodes it. Small readable text is placed inline; large or binary data is written to the workspace and replaced by a file reference. Finally, repeated identical objects in a result are replaced with `same_as` pointers, so the agent sees the same facts without wasting context space.

#### Function details

##### `list_external_tools`  (lines 147–170)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches for connector providers, such as GitHub or Slack, that match the user’s keywords. Someone uses this before choosing a connector, because the available connectors come from the live registry rather than a hard-coded list.

**Data flow**: It receives the current tool context and search queries. It reads the turn’s connector registry, matches local connector IDs and labels, also asks the broker catalog for matches, removes duplicates, and returns a JSON tool result containing connector IDs and labels.

**Call relations**: This is one of the public connector tools. It starts by using `_registry` to get the live connector list, runs catalog searches in parallel with `asyncio.gather`, and finishes by handing the response to `_json_result` so the caller receives normal tool output.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 173–195)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes the real tools available inside one connector. It prevents the agent from guessing tool names by either returning exact schemas for requested tool names or suggesting available tools that match a query.

**Data flow**: It receives a connector ID, optional exact tool names, and an optional search query. It finds the connector entry, asks the broker for schemas for exact names, records any names that do not exist, optionally asks the broker for matching tools, and returns a JSON object with schemas, suggestions, and unresolved names.

**Call relations**: This public tool follows `list_external_tools` in the normal discovery flow. It uses `_registry` to find the connector, `_tool_json` to turn broker tool objects into plain JSON, `_discovery_query` when it needs fallback search words, and `_json_result` to return the final description.

*Call graph*: calls 4 internal fn (_discovery_query, _json_result, _registry, _tool_json).


##### `call_external_tool`  (lines 198–202)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one exact external connector tool using the user’s connected account. It is the gateway from discovery into real action, including side effects like posting a message or reading a repository.

**Data flow**: It receives the connector ID, tool name, account choice, and tool arguments. It finds the connector, resolves which connected account to use, creates a `_ConnectorCall`, and returns the text result from running that call inside a `ToolResult`.

**Call relations**: This public tool is meant to be called after `describe_external_tools` has provided the input schema. It uses `_registry` for the connector lookup, asks `ToolContext.connector_account` to choose the account, then delegates the full execution and file cleanup flow to `_ConnectorCall.run`.

*Call graph*: calls 2 internal fn (connector_account, _registry); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 230–243)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Performs one connector tool execution from start to finish. It stages input files, calls the broker, brings output files back, cleans up encoded data, and shrinks repeated response data.

**Data flow**: It receives the original arguments and an account ID. It replaces any workspace file arguments with broker-ready references, sends the cleaned arguments to the broker’s execute API, downloads any produced files, translates base64 content in the response, adds the downloaded file list if needed, and returns a JSON string ready for the tool result.

**Call relations**: This is the main worker behind `call_external_tool`. It calls `_staged_value` before broker execution, then `_fetched_files` and `_translated_node` after execution, and finally sends `_deduped` to a worker thread so response shrinking does not block the main event loop.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 245–260)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through a tool argument and replaces every workspace-file marker with a broker file reference. This lets users pass files from the workspace without knowing how the broker uploads them.

**Data flow**: It receives any argument value: a plain value, list, dictionary, or a special `workspace_file` dictionary. Plain values pass through unchanged, lists and dictionaries are walked recursively, and a valid workspace-file marker is sent to `_stage_file`; the output has the same shape but with file markers replaced.

**Call relations**: `_ConnectorCall.run` calls this for every top-level tool argument before execution. When it finds an actual file marker, it hands off to `_stage_file` to do the hashing and upload preparation.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 262–293)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Prepares one workspace file so a connector broker can use it. It verifies the file, checks its size, asks the broker where to upload it, and uploads it from inside the sandbox if needed.

**Data flow**: It receives a workspace path string. It converts it to a safe workspace path, runs a small sandbox command to compute the file’s MD5 digest and size, rejects unreadable or oversized files, guesses a content type, asks the broker for an upload slot, optionally performs a `curl` upload from the sandbox, and returns the broker’s argument value for that file.

**Call relations**: This is called only by `_staged_value` when an argument explicitly names a workspace file. It relies on sandbox shell commands, path quoting, and broker upload staging so the serving process coordinates the transfer but does not stream the file bytes itself.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 295–314)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by an external tool into the workspace. It gives the agent stable workspace paths it can read later.

**Data flow**: It receives broker file records, each with a name and download URL. For each one, it chooses a safe filename, creates a fresh unique folder under `/workspace/connector_files/`, downloads the file there from inside the sandbox, and returns a list of names and workspace paths.

**Call relations**: `_ConnectorCall.run` calls this after broker execution, using the broker’s declared file outputs. It uses sandbox `curl` commands so downloaded bytes travel directly from broker storage into the workspace.

*Call graph*: called by 1 (run); 3 external calls (PurePosixPath, quote, uuid4).


##### `_ConnectorCall._translated_node`  (lines 316–376)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Cleans one response object by decoding fields that the provider explicitly marked as base64. This turns unreadable encoded blobs into either readable text or workspace file references.

**Data flow**: It receives a dictionary-like response node and a nesting depth. It looks for provider marker fields such as `encoding: base64`, walks child values, tries to decode marked content fields, chooses a filename and type, translates decoded bytes through `_translated_bytes`, updates the marker to show whether content was inlined or offloaded, and returns the rewritten object.

**Call relations**: `_ConnectorCall.run` starts response translation here, and `_translated` calls it again for nested objects. It uses `_decoded_base64` to safely decode only marked data, `_translated` for child traversal, and `_translated_bytes` to decide whether decoded content stays inline or becomes a file.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 378–397)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Walks any part of a connector response and applies the same cleanup rules everywhere. It keeps deeply nested or ordinary values safe by leaving them alone when they do not need translation.

**Data flow**: It receives any response value plus the current depth. Dictionaries go to `_translated_node`, lists are walked item by item, valid-size `data:` URLs are checked by `_translated_data_url`, and all other values are returned unchanged; if nesting is too deep, it stops walking and returns the subtree as-is.

**Call relations**: `_translated_node` uses this to process children. It is the recursive traffic director for response translation, handing object work to `_translated_node` and embedded data-URL work to `_translated_data_url`.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 399–411)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a single `data:<mime>;base64,...` string when a provider embeds bytes directly in a URL-like string. If the string is not a valid base64 data URL, it leaves it untouched.

**Data flow**: It receives a string. It matches the data-URL pattern, decodes the payload if valid, reads the declared media type or uses a fallback, chooses a fallback filename extension when possible, and returns either inline text or a workspace file reference through `_translated_bytes`.

**Call relations**: `_translated` calls this when it sees a string starting with `data:` and small enough to inspect. It uses `_decoded_base64` for strict decoding and then delegates placement decisions to `_translated_bytes`.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 413–422)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides what to do with decoded bytes from a connector response. Small readable UTF-8 text stays inline; large text or binary data is written to the workspace.

**Data flow**: It receives raw bytes, optional decoded text, a filename, and a media type. If the text exists and is short enough, it returns that text; otherwise it sends the bytes to `_offloaded` and returns the file reference created there.

**Call relations**: Both `_translated_node` and `_translated_data_url` call this after successful base64 decoding. It is the simple policy gate between keeping content in the model’s context and offloading it to a workspace file.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 424–456)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded response bytes into the workspace and returns a reference to the saved file. This keeps binary or large decoded content out of the model’s text context while still making it available.

**Data flow**: It receives a suggested name, media type, and bytes. It sanitizes the filename, creates a content-addressed path using a SHA-256 digest, writes the bytes to a temporary part file through the sandbox, atomically renames it into place, and returns the file name, workspace path, media type, and byte count.

**Call relations**: `_translated_bytes` calls this whenever decoded content should not be inline. It uses hashing for stable paths and sandbox write plus shell rename so repeated identical payloads reuse the same destination without exposing readers to a half-written file.

*Call graph*: called by 1 (_translated_bytes); 4 external calls (sha256, PurePosixPath, quote, uuid4).


##### `_ConnectorCall._deduped`  (lines 458–516)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Turns the final connector response into JSON text and replaces repeated identical objects with pointers to the first copy. This saves context space while preserving the full information.

**Data flow**: It receives the final payload dictionary. It serializes it, skips deduplication if the result is too large to inspect safely, too structurally dense, or already contains the `same_as` key, otherwise walks the payload with `_condensed` and returns JSON for the condensed version.

**Call relations**: `_ConnectorCall.run` calls this at the end in a worker thread. It uses `_escaped` to build safe JSON Pointer paths and `_condensed` to find repeated objects without changing arrays or small values.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 518–594)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Examines one value in a response tree and identifies repeated object structures. When it sees a large object that has appeared before, it replaces the later copy with a `same_as` pointer.

**Data flow**: It receives a value, its JSON Pointer path, the current depth, and a table of first-seen object hashes. It recursively processes dictionaries and lists, computes a secure digest for each node, estimates original size, records large first-time objects, replaces later matching objects with pointers, and returns the rewritten value, digest, and size.

**Call relations**: `_deduped` starts this walk for each top-level payload field. During recursion it calls itself for children and uses `_escaped` for pointer path pieces, making it the core of the response-condensing pass.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 597–600)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one piece of a JSON Pointer path. This makes sure object keys containing `/` or `~` still point to the exact original field.

**Data flow**: It receives a string key. It replaces `~` with `~0` and `/` with `~1`, then returns the escaped token.

**Call relations**: `_deduped` and `_condensed` use this while building `same_as` pointer paths. It is a small helper that keeps deduplication references unambiguous.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 603–627)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Strictly decodes a value only if it is a reasonable-size base64 string. It protects ordinary strings from being accidentally mangled and avoids expensive decoding of very large values.

**Data flow**: It receives any value. Non-strings and over-limit strings return `None`; valid strings have whitespace removed, are base64-decoded with validation, and are then decoded as UTF-8 if possible. The result is either `None` or a pair of raw bytes and optional text.

**Call relations**: `_translated_node` uses this for provider-marked fields, and `_translated_data_url` uses it for embedded data URLs. It is the safe decoding checkpoint before any content is inlined or offloaded.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 630–641)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Searches inside one connector for tools that match a natural-language goal. It is useful when the user knows what they want to do, but not the exact connector tool slug.

**Data flow**: It receives a connector ID and query. It finds the connector entry, asks the broker’s semantic search for matching tools and advice, converts returned tools to JSON, and returns the tools along with any plan, guidance, and pitfalls from the broker.

**Call relations**: This is a public discovery tool alongside `describe_external_tools`. It uses `_registry` to find the connector, `_tool_json` to serialize tool descriptions, and `_json_result` to return the broker’s search response.

*Call graph*: calls 3 internal fn (_json_result, _registry, _tool_json).


##### `_registry`  (lines 644–647)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Returns the connector registry for the current turn. It fails loudly if connector tools were invoked without that registry, because nothing can be safely discovered or called without it.

**Data flow**: It receives the tool context. If the context has a connector registry, it returns it; otherwise it raises an error explaining that connector tools were dispatched without the needed registry.

**Call relations**: All four public connector tools call this at the start of their work. It is the shared guardrail that makes every operation use the live per-turn connector setup.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 650–651)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool description into a plain JSON-ready dictionary. This gives callers the exact slug, human description, and input schema in a consistent shape.

**Data flow**: It receives a broker tool object. It reads its slug, description, and input schema, then returns those fields in a dictionary.

**Call relations**: `describe_external_tools` uses this when returning exact schemas, and `search_connector_tools` uses it for search results. It is the small formatting step between broker objects and tool-result JSON.

*Call graph*: called by 2 (describe_external_tools, search_connector_tools).


##### `_discovery_query`  (lines 654–661)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Builds a useful fallback search query when requested tool names were not found. It turns guessed or wrong slugs into ordinary words that can surface the real tool names.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present, it returns that; otherwise it lowercases unresolved names, replaces non-letter-or-number separators with spaces, removes duplicate words while keeping order, and returns the resulting phrase.

**Call relations**: `describe_external_tools` calls this when it needs to ask the broker for available tools after unresolved names or missing exact tool names. It helps recovery from bad guesses without requiring the caller to start over.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 664–665)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a normal text-based tool result. It is the common final packaging step for discovery-style connector tools.

**Data flow**: It receives a payload dictionary. It serializes the dictionary to a JSON string, puts that string in `TextContent`, wraps it in a `ToolResult`, and returns it.

**Call relations**: `list_external_tools`, `describe_external_tools`, and `search_connector_tools` use this to return their responses in the same format. `call_external_tool` builds a similar result directly because `_ConnectorCall.run` already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack conversation lookup during tool use`

Slack needs several pieces before UFO can answer messages there: a bot token, a signing secret, a known Slack team identity, and proof that Slack can reach this UFO deploy. This file provides tools for getting those pieces in place and for checking where setup stands. There are two setup routes. The OAuth route is the easy path: if this UFO deploy has its own Slack app configured, an admin gets an “Add to Slack” link. The manifest route is the bring-your-own-app path: UFO prints a ready-made Slack app manifest, the user creates that app in Slack, and the secrets are collected privately rather than pasted into chat. Both routes end at the same state: UFO knows the Slack team and bot identity, then waits until Slack sends a signed request to prove the connection works. Think of it like setting up a new phone: first you install the app, then you prove the number belongs to you, then the service confirms calls can reach it. The file also includes a runtime tool, slack_channels, which searches Slack channels and direct messages using the bot token so the agent can find a conversation by name or people instead of needing a raw Slack ID.

#### Function details

##### `_events_url`  (lines 139–140)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to this UFO deploy. It is used anywhere the Slack setup needs to tell Slack where to deliver messages and verification requests.

**Data flow**: It receives the deploy’s public base URL, removes any trailing slash, then adds the Slack surface path. The result is a clean URL like a mailing address for Slack events.

**Call relations**: slack_connect_handler uses it to report the event URL in setup status, and slack_manifest_handler uses it when filling in the Slack app manifest. It is the small shared helper that keeps both setup paths pointing Slack at the same endpoint.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 143–145)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a setup status into the standard tool response format. It gives the agent and user a simple state, a human hint, the Slack event URL, and any extra details such as an install link or missing secrets.

**Data flow**: It receives a state name, a hint message, an optional events URL, and extra fields. It turns them into a JSON text payload and wraps that text in a ToolResult, which is the normal return shape for chat tools.

**Call relations**: slack_connect_handler, _oauth_link, and _derive_manifest_identity call this whenever they need to explain the current Slack setup status. It hands back a uniform response so all install branches speak the same language.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 148–193)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection check and setup flow. A user or agent can call it repeatedly before, during, and after setup, and it will say whether Slack is not configured, not installed, pending, or connected.

**Data flow**: It reads the public URL, checks whether the bot token credential exists, and tries to read the saved Slack identity. If no identity exists, it either starts the OAuth install route or tries to derive identity from bring-your-own-app credentials. Once identity exists, it binds this Slack team to the UFO workspace, checks whether Slack has already reached the deploy with a valid signed request, and returns the appropriate setup state.

**Call relations**: This is the top-level handler behind the slack_connect tool. It calls _events_url for the Slack callback address, _oauth_link for one-click install, _derive_manifest_identity for manifest-based install, _verified to see whether Slack has reached the deploy, and _state to report results back to the chat.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 196–226)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the “Add to Slack” link for the simple OAuth install path. OAuth is the standard web flow where Slack sends the user through an approval screen and then returns a token to UFO.

**Data flow**: It first checks whether this UFO deploy has the needed Slack app client ID and secret in environment variables. It then checks that the speaker is a workspace admin and that the deploy has a public URL. If all checks pass, it creates a short-lived sealed authorization handoff, builds Slack’s install URL, and returns it in a setup state response.

**Call relations**: slack_connect_handler calls this when no Slack identity exists and the requested method is oauth. It depends on ToolContext to start credential authorization, asks Slack surface helpers to build the correct Slack URL, and uses _state to return either a diagnosis or the install link.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 229–263)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-app setup path after the user has privately provided the Slack bot token and signing secret. It proves the token is real by asking Slack who the bot and team are, then saves that identity for later use.

**Data flow**: It checks both required secret slots. If either one is missing, it returns a not_configured message naming what still needs to be collected. If both exist, it reads the bot token, requires the speaker to be an admin, asks SlackIdentityResolver to resolve the team and bot identity, and returns either that identity or a helpful token error message.

**Call relations**: slack_connect_handler calls this when the setup method is manifest and no saved identity exists yet. It uses _state for user-facing status messages and _token_diagnosis when Slack rejects the token so the user gets a clearer next step.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 266–285)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has successfully contacted this UFO deploy using the current signing secret. This separates “we have credentials” from “Slack can actually reach us and the request signature checks out.”

**Data flow**: It looks for a stored verification marker in blob storage for the current workspace. If the marker is missing or unreadable, it returns false. If it exists, it reads the current signing secret credential, fingerprints it, and compares that fingerprint with the one stored in the marker; a match means the current secret is the one Slack used to reach the deploy.

**Call relations**: slack_connect_handler calls this after identity is known. Its answer decides whether the setup status is still pending or fully connected.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, signing_secret_fingerprint, url_verified_blob_key).


##### `slack_manifest_handler`  (lines 288–303)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Generates the exact Slack app manifest a user can paste into Slack when using the bring-your-own-app setup path. A manifest is a structured recipe that tells Slack what permissions, events, and URLs the app needs.

**Data flow**: It receives the desired bot display name and checks that it is short and plain enough for Slack. It reads the deploy’s public base URL, builds the Slack event URL, fills those values into the fixed manifest template, and returns the finished YAML text as the tool result.

**Call relations**: This is the handler behind the slack_app_manifest tool. It calls _events_url so the manifest uses the same Slack event endpoint as the rest of setup, then returns the manifest directly for the user to paste into Slack.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 306–329)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations such as public channels, private channels, direct messages, and group direct messages. It lets the agent find a place to read or post by name, topic, purpose, or people involved instead of requiring the user to provide a Slack ID.

**Data flow**: It reads the stored Slack bot token. If Slack is not connected or identity has not been resolved, it raises a clear error. Otherwise it reads the saved identity, creates a SlackConversationSearch with the bot token, bot user ID, and search query, runs the search, and returns matching conversations as JSON. The result is marked untrusted because names, topics, and purposes come from Slack users, not from UFO itself.

**Call relations**: This is the handler behind the slack_channels runtime tool. It uses read_identity to know which bot is searching, hands the actual Slack API paging and filtering to SlackConversationSearch, then wraps the search results in a ToolResult for the agent.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 332–338)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns a Slack token error code into a clearer message for the user. It is meant to make setup failures actionable instead of exposing only Slack’s short error strings.

**Data flow**: It receives an error code from Slack. If the code means the token is missing, revoked, inactive, or otherwise rejected, it tells the user to re-copy the Bot User OAuth Token. For any other error, it returns a generic auth.test failure message with the code included.

**Call relations**: _derive_manifest_identity calls this when SlackIdentityResolver reports a token-related failure during manifest setup. It supplies the human-readable hint that is then returned through _state.

*Call graph*: called by 1 (_derive_manifest_identity).


### `extensions/yc/ufo_ext_yc/cli.py`

`io_transport` · `request handling`

This file solves two connected problems: signing in to YC, and running YC lookups without leaking or corrupting secrets. Think of it as a locked service window. Users ask for YC information through UFO tools, but the actual work happens in the official `yc` command-line program, with credentials placed in a temporary private home folder for just that run.

For sign-in, the file implements a “device authorization” flow. That means UFO asks YC for a short code and a web link, the admin opens the link in a browser, and UFO later checks whether YC has approved the login. Pending login details are sealed through the credential system, so the raw secret-like data is not simply left in normal storage.

For reads, the file translates simple tool actions like “ask”, “search”, or “skills list” into concrete `yc` commands. It starts the subprocess, limits how much output it can produce, enforces a timeout, and turns failures into clear `YcCliError` messages.

One important detail is credential refresh. The YC CLI may update its token while running. After each command, this file reads the temporary credentials back and carefully rotates the stored credential only if it can do so safely, avoiding overwriting a newer token written by another run.

#### Function details

##### `YcDeviceAuthorization.validate_verification_url`  (lines 59–63)

```
def validate_verification_url(self) -> 'YcDeviceAuthorization'
```

**Purpose**: This checks that YC’s login response points users back to the real Y Combinator account site. It prevents a bad or unexpected response from sending an admin to a different host.

**Data flow**: It reads the verification URL fields already parsed into the authorization object. It chooses the complete URL if present, otherwise the basic URL, checks its host name, and either returns the same object as valid or raises an error.

**Call relations**: When the start-login flow parses YC’s device authorization response, this validator runs as part of that parsing. It protects the later `_start` flow before the URL is shown back to the user.


##### `YcRunner.run`  (lines 94–94)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This is a small contract saying that a YC command runner must know how to run command arguments for a given session and return text output. It lets higher-level code depend on the idea of a runner without caring which concrete runner is used.

**Data flow**: It receives a tuple of command arguments and a session string. A real implementation is expected to execute the command and return the command’s text output.

**Call relations**: YcRead uses this contract so it can hand off prepared YC commands to something that runs them. In production, that runner is YcCli.


##### `YcAuth.run`  (lines 102–115)

```
async def run(self, action: Literal['start', 'complete'], session: str) -> YcAuthResult
```

**Purpose**: This is the front door for YC authorization. It checks that the request is allowed and then routes the work to either starting a login or completing one.

**Data flow**: It receives an action, either `start` or `complete`, plus a session name. It reads the tool context to confirm the extension exists, credentials can be stored, the YC credential slot is declared, and the speaker is an admin. It then returns a structured authorization result.

**Call relations**: The `yc_auth` tool entry calls this function for every authorization request. After its safety checks, it calls `_start` for a new device-code login or `_complete` to check whether the admin finished the browser step.

*Call graph*: calls 2 internal fn (_complete, _start).


##### `YcAuth._start`  (lines 117–175)

```
async def _start(self, session: str) -> YcAuthResult
```

**Purpose**: This starts the YC browser login flow and returns the link and code the admin should use. It also avoids creating duplicate login attempts when the same request is retried.

**Data flow**: It reads any existing pending authorization from extension storage and compares it with the current idempotency key, which is a repeat-request marker. If a matching pending login already exists, it reopens the sealed pending data and returns the same URL and code. Otherwise it asks YC’s authorization server for a new device code, seals the pending data through the credential system, stores a reference to it, and returns the login instructions.

**Call relations**: YcAuth.run calls this when the requested action is `start`. It uses `_credential_digest` to notice whether credentials changed, `_headers` to identify this client to YC, and `_bound` to reject overly large YC responses.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, time).


##### `YcAuth._complete`  (lines 177–229)

```
async def _complete(self, session: str) -> YcAuthResult
```

**Purpose**: This checks whether the admin has finished the YC login in their browser. If successful, it stores the final YC credentials; if not finished yet, it tells the caller the authorization is still pending.

**Data flow**: It reads the pending authorization record from extension storage. If no pending record exists, it checks whether valid credentials are already stored. If pending data exists, it opens the sealed data, checks whether it has expired, calls YC’s token endpoint, and then either stores the returned credentials, reports `pending`, or raises a clear error.

**Call relations**: YcAuth.run calls this when the requested action is `complete`. It uses `_credential_digest` to detect credentials that changed outside this pending flow, `_headers` for the YC HTTP request, and `_bound` to keep response size under control.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 4 external calls (__init__, __init__, loads, time).


##### `YcAuth._credential_digest`  (lines 231–239)

```
async def _credential_digest(self) -> str | None
```

**Purpose**: This creates a fingerprint of the currently stored YC credentials. The fingerprint lets the auth flow tell whether credentials changed without comparing or exposing the credential text itself.

**Data flow**: It tries to read the YC credential slot. If the slot is unset, it returns nothing. If credentials exist, it validates that they have the expected shape, hashes the raw credential text with SHA-256, and returns the hash string.

**Call relations**: _start and `_complete` call this while deciding whether a pending login is still relevant. If the digest changed, they treat YC as already connected and clean up stale pending state.

*Call graph*: called by 2 (_complete, _start); 1 external calls (sha256).


##### `YcAuth._headers`  (lines 241–246)

```
def _headers(self, session: str) -> dict[str, str]
```

**Purpose**: This builds the standard HTTP headers sent to YC’s authorization service. The headers identify the CLI version and tie the request to the current UFO conversation session.

**Data flow**: It receives a session string and returns a dictionary of header names and values. No outside state is changed.

**Call relations**: _start uses these headers when asking for a device code, and `_complete` uses them when exchanging that device code for real credentials.

*Call graph*: called by 2 (_complete, _start).


##### `YcAuth._bound`  (lines 248–250)

```
def _bound(self, response: httpx.Response) -> None
```

**Purpose**: This protects the auth flow from unexpectedly huge HTTP responses. It keeps a bad or broken server response from consuming too much memory.

**Data flow**: It receives an HTTP response and checks the byte length of its content. If the content is within the allowed size, nothing happens; if it is too large, it raises `YcCliError`.

**Call relations**: _start and `_complete` call this immediately after YC responds, before parsing the response body.

*Call graph*: called by 2 (_complete, _start); 1 external calls (__init__).


##### `_read_bounded`  (lines 253–261)

```
async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes
```

**Purpose**: This reads output from a running YC subprocess while enforcing a maximum size. It prevents the external command from flooding UFO with too much text.

**Data flow**: It receives a stream, such as standard output or standard error, and a byte limit. It reads chunks until the stream ends, adds them together, and returns the collected bytes. If the total grows beyond the limit, it raises `YcCliError`.

**Call relations**: YcCli._execute starts this helper for both output streams while the YC command is running. That way command output and error text are watched at the same time.

*Call graph*: called by 1 (_execute); 2 external calls (__init__, read).


##### `YcCli.run`  (lines 269–279)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This runs one YC CLI command with stored credentials in a private temporary environment. It is the main production implementation of the YC command runner.

**Data flow**: It reads the saved YC credentials, validates them, creates a temporary home directory containing those credentials, runs the requested command, saves back any refreshed credentials, and finally deletes the temporary directory. It returns the command’s text output.

**Call relations**: YcRead.run calls this through the YcRunner interface. Inside, it delegates setup to `_prepare_home`, command execution to `_execute`, and token-refresh reconciliation to `_persist_refresh`.

*Call graph*: calls 2 internal fn (_execute, _persist_refresh); 1 external calls (to_thread).


##### `YcCli._prepare_home`  (lines 281–288)

```
def _prepare_home(self, raw: str) -> Path
```

**Purpose**: This creates a temporary private home folder that makes the YC CLI believe it is running as a normal logged-in user. It keeps the real host home directory untouched.

**Data flow**: It receives raw credential JSON. It creates a temporary directory, locks down its permissions, writes the credentials to `.yc/credentials.json`, locks down that file, and returns the temporary home path.

**Call relations**: YcCli.run calls this before launching the external `yc` program. The returned path is later passed to `_execute` and eventually removed during cleanup.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `YcCli._execute`  (lines 290–331)

```
async def _execute(self, home: Path, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This actually starts the external `yc` program and captures its result safely. It turns missing executables, timeouts, too much output, and non-zero exits into clear errors.

**Data flow**: It receives the temporary home path, command arguments, and a session string. It builds a small environment, launches the `yc` subprocess, reads standard output and standard error with size limits, waits up to the configured timeout, and returns decoded output text if the command succeeds.

**Call relations**: YcCli.run calls this after preparing credentials. It relies on `_read_bounded` to consume subprocess output safely while it waits for the command to finish.

*Call graph*: calls 1 internal fn (_read_bounded); called by 1 (run); 5 external calls (__init__, create_subprocess_exec, create_task, gather, timeout).


##### `YcCli._persist_refresh`  (lines 333–352)

```
async def _persist_refresh(self, home: Path, expected: str) -> None
```

**Purpose**: This saves a refreshed YC token back to the credential store after the CLI runs. It carefully avoids overwriting newer credentials if another command refreshed them at the same time.

**Data flow**: It reads the credentials file from the temporary home directory and validates it. If it is unchanged, it does nothing. If changed, it tries to rotate the stored credential from the expected old value to the refreshed value. If another update happened first, it compares token creation times and retries only when the refreshed token is newer.

**Call relations**: YcCli.run calls this after `_execute`, even if the command cleanup is about to happen. This keeps the long-lived stored credentials in sync with any refresh performed by the external YC CLI.

*Call graph*: called by 1 (run); 2 external calls (__init__, to_thread).


##### `YcReadInput.validate_action`  (lines 365–372)

```
def validate_action(self) -> 'YcReadInput'
```

**Purpose**: This checks that a requested YC read action includes the fields that action needs. It catches malformed tool input before any YC command is run.

**Data flow**: It reads the parsed input object. It requires a query for `ask` and `search`, requires a name for `skills_read`, and allows `entity` only for `search`. It returns the same object if valid or raises an error if the request does not make sense.

**Call relations**: This validator runs when tool input is parsed into YcReadInput. YcRead.run can then build commands knowing the required pieces are present.


##### `YcRead.run`  (lines 379–394)

```
async def run(self, args: YcReadInput, session: str) -> str
```

**Purpose**: This turns a simple read request into the exact YC CLI command that should be run. It is the translation layer between tool-friendly actions and command-line arguments.

**Data flow**: It receives a validated YcReadInput object and a session string. It matches the requested action, builds the right tuple of command arguments, optionally adds a search entity filter, then asks its runner to execute the command. It returns the runner’s text output.

**Call relations**: The `yc_read` tool creates YcRead with a YcCli runner and calls this method. This method then hands the prepared command to the runner rather than launching the process itself.


##### `yc_read`  (lines 397–403)

```
async def yc_read(ctx: ToolContext, args: YcReadInput) -> ToolResult
```

**Purpose**: This is the UFO tool function for reading information from YC. It receives a tool request, runs the matching YC CLI command, and wraps the result in the standard tool response format.

**Data flow**: It receives the tool context and parsed YC read arguments. It checks that the YC extension context exists, creates a YcCli-backed YcRead object, passes along a session name based on the conversation, and returns the output as text content inside a ToolResult.

**Call relations**: The tool system calls this when a user or agent invokes the YC read tool. It delegates the real command choice to YcRead.run and the actual subprocess work to YcCli.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `yc_auth`  (lines 406–413)

```
async def yc_auth(ctx: ToolContext, args: YcAuthInput) -> ToolResult
```

**Purpose**: This is the UFO tool function for connecting YC credentials. It starts or completes the admin login flow and returns a small JSON status message.

**Data flow**: It receives the tool context and auth input. It checks that the YC extension context exists, opens an HTTP client with a timeout, calls YcAuth.run with the requested action and conversation session, and returns the resulting status as text content inside a ToolResult.

**Call relations**: The tool system calls this for YC sign-in actions. It creates the HTTP client needed by YcAuth, while YcAuth.run decides whether to call `_start` or `_complete`.

*Call graph*: 4 external calls (__init__, __init__, __init__, AsyncClient).

## 📊 State Registers Touched

- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-security-audit-log` — The durable audit records for sensitive access and administrative/security-relevant actions, distinct from operational traces.
- `reg-external-client-pools` — The live reusable HTTP/provider client sessions and connection pools for model and connector calls, including lifecycle cleanup handles.
- `reg-turn-cleanup-callback-state` — The per-turn registry of cleanup callbacks and borrowed-resource finalizers that tools add during execution and completion/teardown later drains.
- `reg-source-connector-registry` — The registered source backend implementations, credential requirements, sync hooks, and capability metadata used to instantiate external content synchronization.
- `reg-connector-action-catalog` — The dynamic catalog/cache of hosted connector actions, MCP-discovered tools, schemas, and routing metadata available for connected external accounts.

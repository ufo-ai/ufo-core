# Brokered connector discovery and action calls  `stage-11.1`

This stage is the system’s brokered doorway to outside apps. It is shared behind-the-scenes support used when an agent needs to find a connector, link an account, inspect available actions, run an action, or move files safely. The key idea is that the agent can use services like Gmail, GitHub, Slack, Datadog, or an MCP server without directly seeing private tokens or passwords.

The core access files set the rules. connectors.py decides how tools and feed-sync jobs may talk to outside services. grants.py records who connected which account, which agent may use it, and how access can be shared, revoked, or disconnected.

The connector tools extension gives agents a searchable front door: find connectors, inspect real tools, run them through a broker, and stage files in or out. Composio and Pipedream each add a broker plus a client. The clients talk to those platforms; the brokers translate UFO’s requests into provider calls and return results and files. Composio also supports proxy HTTP calls and one-shot MCP tool calls. Keyed connectors declare API-key services. The MCP extension discovers and runs tools from workspace-configured MCP servers.

## Files in this stage

### Access and grant model
Core access primitives define how connector calls obtain permission, use connected accounts, and enforce account ownership safely.

### `core/src/ufo/runtime/access/connectors.py`

`domain_logic` · `cross-cutting: connector discovery, tool execution, and feed-sync authentication`

This file is about one central safety rule: outside-service credentials must go only where they are needed, and never into logs, sandboxes, or agent-visible data. A connector may authenticate in a few ways. Sometimes a broker, such as Composio or Pipedream, keeps the real token and only provides a special HTTP transport that adds the token on the server side. Sometimes the workspace owns a direct API key, and the sync job reads it as a bearer token or special headers. The `Credential` object represents exactly one of those choices.

The file also defines the broker interface. A broker can list tools, describe a tool’s input shape, run a tool for a connected account, prepare file uploads, expose produced files as temporary links, and provide credentials for feed syncing. Think of the broker like a hotel front desk: the agent asks for a service, but the front desk holds the key and opens the right door without handing the key to the guest.

`ConnectorRegistry` is the routing table. It knows which provider belongs to which broker, can ask an open resolver about providers that were not registered one by one, and can fall back to direct credentials when a source uses a workspace-owned key. The later helper classes add an important guard: a sync source bound to a specific member-owned connection must prove that connection is still active before every brokered request.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a credential for debugging. It deliberately hides tokens, headers, and transport details so an accidental log message cannot reveal a secret.

**Data flow**: It reads which authentication path is present on the `Credential` object: proxy transport, bearer token, custom headers, or none. It turns that into a short label such as `Credential(<bearer: redacted>)`, with the sensitive value replaced by the word `redacted`.

**Call relations**: This method is used automatically by Python when a `Credential` is printed or shown in an error. It protects all code that carries credentials, including broker credential resolution and direct credential resolution, from leaking secrets through ordinary object display.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the common promise for anything that can produce a `Credential` for a feed-sync source. A sync job uses it to ask, “How should I authenticate to this provider for this workspace and account?”

**Data flow**: The caller provides a workspace ID, provider name, and account handle. An implementation looks up or prepares the right authentication method and returns a `Credential`, such as a proxy transport, bearer token, or headers.

**Call relations**: This is a protocol, meaning it describes the shape other classes must follow. `SourceCredentialResolver.bind` returns an object that follows this protocol, and `_credential` calls either a broker or fallback implementation that follows the same promise.


##### `GrantUnusable.__init__`  (lines 113–115)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an error that means a connected account cannot currently be used and likely needs human reconnection. It also records whether the system should wait specifically for a reconnection event before trying again.

**Data flow**: It receives a human-readable reason and an optional `awaits_grant` flag. It stores the reason in the normal exception message and saves the flag on the exception object for later decision-making.

**Call relations**: Broker integrations such as Composio and Pipedream raise this when they discover an account grant is revoked, expired, unhealthy, or unknown. Higher-level sync code can treat it differently from a temporary service outage, avoiding noisy retries when only a member can fix the problem.

*Call graph*: called by 4 (credential, _account, credential, _account).


##### `stale_grant_guidance`  (lines 118–125)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error-message suffix for the case where a broker no longer recognizes an account grant. The message tells the reader that reconnecting the account is the likely fix.

**Data flow**: It takes a provider name and inserts it into a fixed explanatory sentence. It returns that sentence as plain text.

**Call relations**: Broker code can use this helper when raising account-grant errors. It keeps the guidance consistent across broker implementations, especially for cases caused by switching broker configuration or using an old connection.


##### `ConnectorBroker.tools`  (lines 196–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists available tools for a provider, optionally narrowed by a search query. These tools are what the agent can discover and later ask to run.

**Data flow**: The caller supplies a workspace ID, provider name, and query text. An implementation asks its broker service for matching tools and returns `BrokerTool` entries with names and descriptions.

**Call relations**: This protocol method is implemented by broker extensions. Dynamic connector discovery uses it when showing the agent what actions are available for a connected provider.


##### `ConnectorBroker.schema`  (lines 200–200)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker explains the inputs for one specific provider tool. The agent needs this so it can build valid arguments before asking the broker to run the tool.

**Data flow**: The caller gives a workspace ID, provider name, and tool slug. The broker returns a `BrokerTool` that includes the input schema, or raises `UnknownBrokerTool` if that tool name is not known.

**Call relations**: Dynamic connector tooling calls this during tool description. It turns a tool name into enough structure for the agent to call it correctly.


##### `ConnectorBroker.execute`  (lines 202–210)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one tool against a connected account. The broker, not the agent sandbox, holds and injects the real account token.

**Data flow**: The caller sends the workspace ID, provider, tool slug, argument values, account ID, and an optional idempotency key, which is a repeat-safe request key. The broker performs the provider action and returns a dictionary response.

**Call relations**: Dynamic connector tools call this after discovery and argument preparation. File staging and file output helpers around it make sure large file bytes travel by temporary URLs rather than through the main server process.


##### `ConnectorBroker.file_outputs`  (lines 212–212)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts produced files from a tool response. It turns broker-specific response data into a common list of downloadable file references.

**Data flow**: It receives the raw dictionary returned by `execute`. An implementation reads whatever file information its broker includes and returns `BrokerFile` objects containing filenames and temporary URLs.

**Call relations**: After a brokered tool runs, dynamic connector code can call this to tell the sandbox where to fetch produced files. The actual bytes are fetched directly from the broker’s file store, not carried through this file’s code.


##### `ConnectorBroker.stage_upload`  (lines 214–222)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file so a provider tool can consume it. It creates a temporary upload destination or reuses an already-stored file.

**Data flow**: The caller provides workspace ID, provider, tool slug, filename, MIME type, and MD5 checksum. The broker returns a `StagedUpload` with a PUT URL if bytes need uploading, a content type, and the argument value to place into the tool call.

**Call relations**: Dynamic connector tools call this before executing a tool that needs file input. The sandbox uploads the file bytes directly to the broker’s storage, then `execute` receives only a reference to that staged file.


##### `ConnectorBroker.search`  (lines 224–224)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines a richer, meaning-based search over broker tools. It can return matching tools plus a suggested plan, guidance, or warnings.

**Data flow**: The caller sends workspace ID, provider, and a query. The broker returns a `BrokerSearch` containing matching tools and any extra advice the broker can provide.

**Call relations**: Dynamic connector discovery can use this when a broker supports smarter routing than simple text matching. Brokers without such a router can return an empty or basic result.


##### `ConnectorBroker.credential`  (lines 226–226)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker provides the authentication route for a feed-sync source using a connected account. The returned credential should normally be a proxy transport so the real token stays with the broker.

**Data flow**: The caller supplies workspace ID, provider, and account handle. The broker verifies the account belongs to that workspace and returns a `Credential` that lets the connector make provider requests safely.

**Call relations**: _credential` calls this when a source is using a brokered account rather than direct workspace credentials. `_BoundSourceCredentials.credential` then wraps the returned transport with an extra connection-validity check.


##### `GrantSecret.secret`  (lines 236–236)

```
async def secret(self, workspace_id: UUID, account_id: str) -> str
```

**Purpose**: Defines the rare path where the deploy needs to read the real token for a connected account, usually so an egress proxy can swap it into an outgoing request. The sandbox still never receives the secret.

**Data flow**: The caller gives a workspace ID and account ID. An implementation verifies the account belongs to that workspace and returns the real secret string or raises an error if it cannot authenticate it.

**Call relations**: Connector declarations for command-line tools can depend on this protocol. The egress proxy uses it behind the scenes when replacing a harmless placeholder with the actual provider token on the wire.


##### `ConnectorResolver.transfer_hosts`  (lines 306–306)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Names the broker file-store hosts that grants in an open connector namespace are allowed to use. This matters for safe file transfer through the network egress controls.

**Data flow**: An implementation returns a tuple of hostnames. No input is needed beyond the resolver’s own configuration.

**Call relations**: The registry can include a resolver for providers that are not registered one by one. The transfer hosts from that resolver tell the egress layer which broker storage locations are expected for uploads and downloads.


##### `ConnectorResolver.claims`  (lines 308–308)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Answers whether the resolver’s broker can serve a given provider slug. This prevents the system from assuming an open namespace owns every unknown provider.

**Data flow**: It receives a provider name. The implementation checks the broker’s live catalog or rules and returns true if this resolver can serve it, false otherwise.

**Call relations**: Code choosing between brokered connection handling and direct workspace credentials can ask this before routing. It is part of the open-provider side of the connector registry.


##### `ConnectorResolver.entry`  (lines 310–310)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a registry entry for a provider served by an open resolver. It lets one broker support many provider slugs without listing each one in static configuration.

**Data flow**: It receives a provider name and returns a `ConnectorEntry` pointing that provider to the shared broker, with a label for display.

**Call relations**: `ConnectorRegistry.entry` and `_broker` call through to this when a provider is not found in the fixed entries but a resolver exists. That is how dynamic, broker-catalog providers join the same routing path as explicitly registered ones.


##### `ConnectorResolver.catalog`  (lines 312–312)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Pages through the resolver broker’s live catalog of connectable services. This lets discovery show services that were not hard-coded into the registry.

**Data flow**: The caller supplies search text, a maximum number of results, and an optional cursor named `after` for continuing a previous page. The resolver returns a `CatalogPage` with entries and possibly a next cursor.

**Call relations**: `ConnectorRegistry.search_catalog` and `ConnectorRegistry.catalog` call this to combine fixed registered providers with the open broker namespace.


##### `ConnectorRegistry.entry`  (lines 329–335)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the routing entry for a provider. It first checks explicitly installed connectors, then falls back to the open resolver if one exists.

**Data flow**: It receives a provider name. It returns the matching `ConnectorEntry` if found directly or built by the resolver; if neither path exists, it raises a `KeyError` explaining that no connector is installed for that provider.

**Call relations**: Dynamic connector execution uses this kind of lookup to choose the broker for a provider. `_broker` performs similar routing when credential resolution needs just the broker object.


##### `ConnectorRegistry.search_catalog`  (lines 337–342)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns searchable providers from the open resolver only. It is used to append live broker-catalog results to the system’s fixed connector list.

**Data flow**: It receives search text and a limit. If there is no resolver, it returns an empty tuple; otherwise it asks the resolver for the first catalog page and returns that page’s entries.

**Call relations**: Discovery flows use this when they want extra connectable services beyond registered connectors. It delegates all live catalog work to `ConnectorResolver.catalog`.


##### `ConnectorRegistry.catalog`  (lines 344–364)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Builds one combined page of connectable services from both explicitly registered connectors and the open resolver. It also removes duplicates so the same provider does not appear twice.

**Data flow**: The caller supplies search text, a result limit, and an optional continuation cursor. On the first page, it filters registered entries by provider or label text. It also asks the resolver for its page if present, merges the two lists, keeps the first entry for each provider, and returns a `CatalogPage` with the resolver’s next cursor.

**Call relations**: Connector discovery calls this to show members what services they can connect. Internally it creates catalog entries for registered connectors and combines them with the resolver’s live page.

*Call graph*: 2 external calls (__init__, __init__).


##### `_broker`  (lines 367–373)

```
def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None
```

**Purpose**: Looks up the broker responsible for a provider, if one is available. It is a small routing helper for credential resolution.

**Data flow**: It receives the registry and provider name. It checks fixed registry entries first, then asks the resolver for an entry if present, and returns the broker; if no route exists, it returns `None`.

**Call relations**: _credential` calls this when an account is not the direct-account marker. This keeps the broker lookup logic in one place before asking the broker for a credential.

*Call graph*: called by 1 (_credential).


##### `_credential`  (lines 376–389)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right way to obtain a credential for a sync source. Brokered accounts go through their connector broker, while the special direct account goes through the fallback direct-auth backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not `DIRECT_ACCOUNT`, it finds a broker and asks it for a credential; if no broker exists, it raises an error. If the account is `DIRECT_ACCOUNT`, it asks the fallback auth proxy; if no fallback exists, it raises an error.

**Call relations**: _BoundSourceCredentials.credential` calls this after checking whether the source is allowed to use the requested account. `_credential` relies on `_broker` for broker routing.

*Call graph*: calls 1 internal fn (_broker); called by 1 (credential).


##### `_require_source_connection`  (lines 392–416)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Verifies that a feed-sync source is still allowed to use a specific member-owned connection. This prevents an old or revoked source from continuing to make brokered provider requests.

**Data flow**: It receives workspace ID, connection ID, owner member ID, provider, and account. It opens a workspace-scoped database transaction, searches the connection table for an active matching row, and returns nothing if found. If no matching connection exists, it raises a `ValueError`.

**Call relations**: _BoundSourceCredentials.credential` calls this before returning a brokered credential, and `_ConnectionTransport.handle_async_request` calls it before each HTTP request. Together those checks cover both credential creation time and actual request time.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 428–436)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Wraps a broker-provided HTTP transport with a last-second permission check. Before any provider request is sent, it confirms the source’s connection is still active.

**Data flow**: It receives an outgoing HTTP request. It first calls `_require_source_connection` using the bound workspace, connection, owner, provider, and account details; if the check passes, it forwards the request to the inner transport and returns the HTTP response.

**Call relations**: _BoundSourceCredentials.credential` creates this wrapper around broker credentials. It hands off the actual network behavior to the original transport but adds the database authorization check in front of every request.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 438–439)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the caller is done with it. This releases any network resources owned by the inner transport.

**Data flow**: It receives no new data besides the transport object itself. It calls `aclose` on the inner transport and returns nothing.

**Call relations**: HTTP clients call this as part of cleanup. The wrapper does not own separate resources; it simply passes the close request to the transport it wraps.


##### `_BoundSourceCredentials.credential`  (lines 448–478)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Produces credentials for a feed-sync source, while enforcing whether that source is allowed to use direct credentials or a specific member-owned connection. It is the main safety gate for source authentication.

**Data flow**: It receives workspace ID, provider, and account. If the account is the direct marker, it rejects any source that was bound to a connection, then returns the direct credential. If the account is brokered, it requires stored connection and owner IDs, verifies the connection in the database, asks `_credential` for the broker credential, ensures that credential uses a proxy transport, and returns a new `Credential` whose transport is wrapped with `_ConnectionTransport`.

**Call relations**: Objects of this class are created by `SourceCredentialResolver.bind`. Sync runners call its `credential` method through the `AuthProxy` protocol, and it delegates to `_require_source_connection` and `_credential` before returning a request-time-checked transport.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 485–490)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an auth proxy tied to one feed source’s connection information. This makes later credential requests remember which connection, if any, the source is allowed to use.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those IDs together with the connector registry into a `_BoundSourceCredentials` object and returns it as an `AuthProxy`.

**Call relations**: The sync runner uses this when preparing a source run. The returned object later performs the actual direct-or-broker credential choice and connection checks in `_BoundSourceCredentials.credential`.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/access/grants.py`

`domain_logic` · `connect flow, request handling, and connection administration`

This file is the project’s control center for “connect an account” flows. In plain terms, it is the part that turns a member pressing a Connect button into a saved account connection that an agent can use, without giving the agent the secret token directly. It uses OAuth, which is the common web pattern where a service sends the user to another site to approve access and then returns with a code.

The main path has two halves. First, `ConnectFlow.authorize` creates a provider approval link and seals important details into the OAuth “state” value: which workspace, which agent, which member, which conversation, and whether the connection should be shared. “Sealed” here means encrypted and signed so the callback can trust it later without keeping a temporary database row. Second, `ConnectFlow.complete` opens that state, exchanges the provider’s code for a broker-side account, records the connection and grant in the database, wakes any extension hooks, and optionally posts a message back into the conversation.

`GrantStore` is the database worker. It makes sure accounts stay owned by the right member, adds or removes agent grants, controls sharing, and cleans up feed sources when a connection is removed. The file also includes smaller helpers for naming accounts, choosing usable CLI accounts, and exposing summaries for audit or operator views.

#### Function details

##### `grant_sentinel`  (lines 49–53)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a placeholder credential value for a connected account. The sandbox can receive this harmless placeholder, and another layer can later swap it for the real server-held token.

**Data flow**: It takes an account id as text, adds a fixed project-specific prefix, and returns the combined string. It does not read or change any stored state.

**Call relations**: This helper supports the broader grant system by giving the execution environment and the outbound proxy a shared way to refer to the same connected account without passing the actual secret.


##### `usable_cli_accounts`  (lines 56–76)

```
def usable_cli_accounts(grants: 'tuple[Grant, ...]', provider: str, member_id: UUID | None) -> tuple[str, ...]
```

**Purpose**: Chooses which connected accounts a command-line tool may use for one provider. It prefers the member’s own private connection, and falls back to shared workspace connections.

**Data flow**: It receives a set of grant records, a provider name, and an optional member id. It filters grants for that provider, sorts private matches for that member, sorts shared matches, and returns the private list if any exist, otherwise the shared list.

**Call relations**: This is used when building a tool environment: it decides whether a static environment variable can name exactly one account, while still allowing lower layers to know about all usable grant-backed accounts.


##### `UnknownProvider.__init__`  (lines 84–85)

```
def __init__(self, provider: str) -> None
```

**Purpose**: Creates a clear, member-facing error when someone asks to connect a provider that this deployment does not know how to support.

**Data flow**: It receives the provider slug, formats it into a friendly sentence, and stores that sentence on the exception.

**Call relations**: Provider lookup code raises this when neither the installed provider map nor the open provider resolver can supply a connector.

*Call graph*: called by 2 (_provider, validate_provider).


##### `OAuthProvider.provider`  (lines 128–128)

```
def provider(self) -> str
```

**Purpose**: Defines the provider name that a concrete OAuth connector must expose. This is the stable internal name saved with connections and grants.

**Data flow**: A real provider implementation returns its provider string. The protocol itself only states that this value must exist.

**Call relations**: Connect flow code reads this after resolving a provider so the database records the canonical provider name, not just whatever text came in from the request.


##### `OAuthProvider.host`  (lines 131–131)

```
def host(self) -> str
```

**Purpose**: Defines the provider host that a concrete connector must expose. This tells the egress-control layer which outside host the grant allows.

**Data flow**: A real implementation returns a host string. Some brokered providers may return an empty host when no direct host access is granted.

**Call relations**: After OAuth completes, the connect flow stores this value with the connection so later outbound access can be checked against the grant.


##### `OAuthProvider.authorize_url`  (lines 133–133)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a provider builds the web link that sends a member to approve access.

**Data flow**: It receives sealed state and the callback address. A provider implementation combines those with provider-specific OAuth details and returns a URL for the browser.

**Call relations**: The authorization half of the connect flow calls this after it has prepared trustworthy state for the callback.


##### `OAuthProvider.exchange`  (lines 135–137)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a provider turns the returned OAuth code into a stable connected account record.

**Data flow**: It receives the temporary code, callback address, workspace id, and sealed state. A provider implementation talks to the broker or provider, verifies the account belongs in this workspace, and returns an `OAuthAccount` with an account id and optional label.

**Call relations**: The completion half of the connect flow calls this before anything is recorded, because the database grant needs to know which provider account was actually approved.


##### `OAuthProviderResolver.claims`  (lines 148–148)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Asks an open provider namespace whether it can serve a provider slug. This lets a broker support many provider names without registering each one up front.

**Data flow**: It receives a provider name and returns true or false, possibly after checking an external catalog.

**Call relations**: Provider validation uses this when the provider is not in the fixed installed map, so typos fail before a dead authorization link is minted.


##### `OAuthProviderResolver.descriptor`  (lines 150–150)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuth provider descriptor for a provider slug claimed by an open namespace.

**Data flow**: It receives the provider name and returns an object that follows the `OAuthProvider` shape.

**Call relations**: Provider lookup falls back to this resolver when no explicit connector is installed under the requested name.


##### `ConnectionHooks.fire`  (lines 257–257)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Defines the hook point where extensions learn that a connection has landed. Extensions can use this to create follow-on state, such as feed source rows.

**Data flow**: It receives a `ConnectionRecorded` payload and performs extension-specific work. The protocol promises no direct return value.

**Call relations**: After `ConnectFlow.complete` commits the connection and grant, it calls this hook if one is installed, so extension-derived state can exist before the member sees the callback result.


##### `ConnectResumption.resume`  (lines 270–277)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Defines how the system posts back into the conversation that asked for the connection. This lets an agent continue after the member returns from the browser approval flow.

**Data flow**: It receives the conversation id, message text, member speaker id, and an idempotency key, which is a repeat-safe key used to avoid duplicate messages. It returns whether a resume message was actually queued or admitted.

**Call relations**: The connect completion path calls this last, after the connection and extension work are done, so the resumed conversation sees the account as already usable.


##### `_resume_key`  (lines 280–295)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a repeat-safe message key for one OAuth connect attempt. It is based on the sealed state, not the connection id, because several separate connect attempts can land on the same connection.

**Data flow**: It takes the sealed state string, hashes it with SHA-256, keeps a short digest, prefixes it, and returns that as the idempotency key.

**Call relations**: The connect completion path uses this when asking the resumption layer to post the “Connected …” message, so refreshing the callback does not create duplicate conversation messages.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 322–323)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently in scope. The store uses this so all database reads and writes stay inside the active workspace.

**Data flow**: It reads the current workspace context and returns its workspace id.

**Call relations**: Almost every `GrantStore` database operation relies on this property to add the correct workspace boundary to its queries.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 326–327)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent currently targeted by object dispatch. This is the agent that receives or loses a grant.

**Data flow**: It reads the current object-scope agent id and returns it.

**Call relations**: Grant creation, listing, revocation, and sharing checks use this so a command aimed at one agent does not accidentally affect another.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 329–520)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None, landed_turn_id: UUID | None=None) ->
```

**Purpose**: Saves a completed connection and grants the current agent access to it. It also reactivates parked feed sources that may have been waiting for the member to reconnect.

**Data flow**: It receives provider/account details, the granting member, conversation, sharing choice, optional account label, and optional turn id. It checks the member still has access, inserts or reuses the connection, refuses to take over an account owned by another member, upserts the agent grant, optionally stamps the turn as answered, clears relevant parked-source retry state, and returns the connection id.

**Call relations**: `ConnectFlow.complete` calls this after the OAuth provider has exchanged the code for an account. The method is the durable landing point for the flow: once it succeeds, later hooks and conversation resumption can safely act on the saved connection.

*Call graph*: 10 external calls (__init__, __init__, now, and_, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 522–567)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists all active connection grants for the currently targeted agent. This tells tool execution which outside accounts the agent may use.

**Data flow**: It reads grant, connection, and owner-member rows for the current workspace and agent. It turns each joined database row into a `Grant` object and returns them as a tuple.

**Call relations**: The sandbox environment builder calls this when preparing command-line credentials, so only the agent’s granted accounts become available.

*Call graph*: called by 1 (_grant_cli_env); 4 external calls (__init__, and_, select, workspace_tx).


##### `GrantStore.revoke`  (lines 569–581)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the acting member is allowed to do so.

**Data flow**: It receives a grant id and actor member id. It checks that the grant belongs to the current agent and that the actor owns or may administer the underlying connection, deletes the grant row if allowed, and returns whether a row was removed.

**Call relations**: User-facing revoke actions call this to remove an agent’s access while leaving the underlying member-owned connection intact.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 583–640)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Gives the current agent access to an already existing connection. It allows this only when the actor owns the connection or the connection is already shared.

**Data flow**: It receives provider, account id, conversation id, actor member id, and a requested sharing flag. It finds and locks the connection, checks permissions, refuses to widen sharing during attach, inserts the grant if it is not already present, and returns true if the connection existed.

**Call relations**: This supports flows where a member or surface selects an existing connected account for an agent instead of running OAuth again.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 642–670)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the underlying connection is shared with the workspace audience. Sharing is controlled carefully because it can broaden who may attach and use the account.

**Data flow**: It receives a grant id, the desired shared value, and the acting member id. It verifies the actor may mutate the connection, updates the connection’s shared flag, and returns whether the update happened.

**Call relations**: Grant administration calls this after finding a specific grant edge; internally it relies on the same actor-checking path used by revocation.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 672–728)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a connection from the workspace and retires feed sources tied to it. This is stronger than revoking one agent’s grant.

**Data flow**: It receives a connection id and actor member id. It checks permission, finds sources tied to the connection, deletes their source grants, marks sources removed, tombstones their pages, deletes the connection row, and returns whether the operation was allowed and completed.

**Call relations**: Connection administration uses this when a member or admin wants the account connection gone entirely. Deleting the connection also removes connector grants through database relationships.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 730–758)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific connection. It centralizes the ownership and admin rule so higher-level operations do not repeat it.

**Data flow**: It receives a database connection, connection id, actor member id, and whether admin power is allowed. It locks and reads the connection, returns none if missing, returns the id if the actor owns it, otherwise checks admin status and either returns the id or raises a permission error.

**Call relations**: `disconnect` uses this directly, and grant-level checks use it through `_grant_for_actor`.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 760–770)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Answers whether a member is an admin in the current workspace.

**Data flow**: It receives a database connection and member id, reads the member row’s admin flag for the current workspace, and returns true or false.

**Call relations**: `_connection_for_actor` calls this only when the actor is not the connection owner and admin authority might be accepted.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 772–810)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a grant belongs to the current agent and whether the actor may change the connection behind it.

**Data flow**: It receives a database connection, grant id, actor member id, and an admin-allowed flag. It looks up the grant’s connection, delegates connection permission checking, then locks and returns the grant id if it still matches the current workspace, agent, and connection.

**Call relations**: `revoke` and `set_shared` call this before changing anything, so both operations use the same permission and current-agent safety rules.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 831–853)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, turn_id: UUID | None=None) -> str
```

**Purpose**: Creates the provider approval URL for a new connect attempt. This is the first half of OAuth: send the member to approve access.

**Data flow**: It receives workspace, agent, provider, member, conversation, sharing choice, and optional turn id. It resolves the provider, packs those facts into `ConnectState`, encrypts that state, asks the provider to build an authorization URL, and returns the URL.

**Call relations**: `ConnectHandoff.authorize` calls this when a terminal connect request needs a fresh live URL for the member to press.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 855–860)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks that a requested provider is available before the system offers a connect action.

**Data flow**: It receives a provider name. It accepts names in the installed provider map, accepts names claimed by an optional resolver, and otherwise raises a friendly unknown-provider error.

**Call relations**: Connect-request creation can call this early, while later URL minting uses the cheaper `knows_provider` check under a database lock.

*Call graph*: calls 1 internal fn (__init__).


##### `ConnectFlow.knows_provider`  (lines 862–867)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Performs a quick local check that provider support still exists. It avoids slower external validation while the turn row is locked.

**Data flow**: It receives a provider name and returns true if the provider is explicitly installed or if an open resolver exists.

**Call relations**: `ConnectHandoff.authorize` uses this each time a member presses a connect control, making sure the request still points to something this process can serve.


##### `ConnectFlow.bridge_workspace`  (lines 869–875)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to run as.

**Data flow**: It receives sealed state, a provider name, and a callback address. It opens the state, checks that the provider and callback match what was sealed, verifies the provider can be resolved, and returns the workspace id.

**Call relations**: `connect_bridge_workspace` wraps this for HTTP requests, returning none instead of leaking detailed errors when the bridge request is invalid.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 877–927)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth handoff after the provider redirects back. It records the connected account, grants the agent, fires connection hooks, and optionally resumes the conversation.

**Data flow**: It receives sealed state and the provider code. It opens the state, resolves the provider, enters the sealed workspace and agent scope, exchanges the code for an account, records the connection and grant, notifies hooks, builds a member-friendly connected message, attempts conversation resumption, and returns a `GrantRecorded` summary.

**Call relations**: This is the main callback path. It sits between provider-specific OAuth exchange, `GrantStore.record` for durable storage, extension hooks for derived state, and the conversation resumption layer.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 929–934)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns the display name that members should see for a provider.

**Data flow**: It receives a provider slug. It returns a configured label if present, otherwise turns underscores into spaces and title-cases the words.

**Call relations**: `ConnectFlow.complete` uses this when composing the callback and conversation message, so users see a readable name instead of an internal slug when possible.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 936–942)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. This is the common lookup path for authorization, callback verification, and completion.

**Data flow**: It receives a provider name, returns a descriptor from the installed provider map if present, otherwise asks the resolver if one exists, and raises an unknown-provider error if not.

**Call relations**: `authorize`, `bridge_workspace`, and `complete` all call this before they rely on provider-specific behavior.

*Call graph*: calls 1 internal fn (__init__); called by 3 (authorize, bridge_workspace, complete).


##### `ConnectFlow._open`  (lines 944–949)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state from a browser redirect.

**Data flow**: It receives the state string, asks Fernet to decrypt it within the allowed time window, parses the JSON into `ConnectState`, and returns it. If the state is expired or tampered with, it raises a connect-state error.

**Call relations**: Both bridge verification and OAuth completion call this before trusting any workspace, member, agent, provider, or conversation id from the browser.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 977–1062)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Serves the private connect URL for a specific terminal turn. It reuses a recent URL when safe and mints a new one when the old state may be too close to expiry.

**Data flow**: It receives workspace id, turn id, and member id. It loads and locks the turn, verifies the turn still contains a connect request for that member, checks the provider and target agent still exist, returns a still-fresh memoized URL if present, otherwise creates a new URL through `ConnectFlow.authorize`, saves it on the turn, and returns the winner in case of a race.

**Call relations**: Surfaces call this when a member presses or views a connect control. It protects the connect request from stale providers, deleted agents, wrong members, and expired authorization state.

*Call graph*: calls 1 internal fn (_held); 5 external calls (__init__, model_validate, select, update, workspace_tx).


##### `ConnectHandoff._held`  (lines 1064–1075)

```
def _held(self, url: str | None, authorized_at: datetime | None) -> str | None
```

**Purpose**: Decides whether a saved authorization URL is still fresh enough to hand back.

**Data flow**: It receives a URL and the time it was minted. If either is missing, or if the timestamp is older than the memo window, it returns none; otherwise it returns the URL.

**Call relations**: `ConnectHandoff.authorize` calls this before minting and again after a possible database race, so repeated presses share one live control when appropriate.

*Call graph*: called by 1 (authorize); 3 external calls (now, replace, timedelta).


##### `install_connect_flow`  (lines 1081–1089)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow object. This avoids passing the same configured provider map, encryption key, store, and callback URL through every caller.

**Data flow**: It receives a `ConnectFlow` or none and stores it in a module-level variable. It returns nothing.

**Call relations**: Startup code or tests use this to configure the singleton that later connect tools and callback helpers read.


##### `installed_connect_flow`  (lines 1092–1095)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed connect flow, or fails loudly if connect support is not configured.

**Data flow**: It reads the module-level flow variable. If one is present, it returns it; if not, it raises a `ConnectUnavailable` error.

**Call relations**: `connect_bridge_workspace` uses this before verifying bridge requests, and other runtime paths can use it to reach the configured connect flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 1098–1107)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Extracts and verifies the workspace for a connector browser bridge HTTP request. Invalid requests are rejected by returning none.

**Data flow**: It reads `state`, `provider`, and `callback` query parameters from the request, passes them to the installed connect flow’s bridge verifier, and returns the workspace id. If the flow is missing, the state is bad, or the provider is unknown, it returns none.

**Call relations**: HTTP bridge handling can call this early to decide which workspace context, if any, the browser bridge is allowed to enter.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 1114–1123)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. It keeps names consistent across connection objects, grant objects, and prepared portal intents.

**Data flow**: It receives a provider and account id, slugifies both, truncates the readable part to fit the object-name limit, adds a short hash digest to prevent collisions, and returns the final name.

**Call relations**: Any surface that needs to refer to the same connected account as an object can use this helper and get the same safe name every time.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 1126–1127)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a lowercase dash-separated slug suitable for object names.

**Data flow**: It receives raw text, lowercases it, replaces runs of non-letter-or-digit characters with dashes, trims extra dashes, and returns the slug.

**Call relations**: `account_object_name` uses this for both the provider and account parts before adding the collision-preventing digest.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 1130–1138)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of connector grants for the currently targeted agent.

**Data flow**: It builds a scope for the current workspace and current object-target agent, delegates the database query to `_grant_summaries`, and returns the resulting summary objects.

**Call relations**: Agent-scoped administrative or portal views use this when they need to show which accounts that agent can use.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 1141–1144)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of all connector grants in one workspace. This is broader than the current-agent view.

**Data flow**: It receives a workspace id, temporarily enters that workspace context, asks `_grant_summaries` for all grants in that workspace, and returns the summaries.

**Call relations**: Operator-level surfaces use this when they need a workspace-wide grant inventory instead of an agent-specific one.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 1147–1198)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into readable audit summaries.

**Data flow**: It receives a SQL scope condition. It joins grants to connections, agents, and owner members, orders by provider and agent name, converts each row into a `GrantSummary`, and returns the tuple.

**Call relations**: Both `grant_summaries` and `workspace_grant_summaries` delegate here so the two views share one consistent summary shape.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 4 external calls (__init__, and_, select, workspace_tx).


##### `connection_summaries`  (lines 1201–1278)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the workspace’s member-owned connected accounts and the agents currently granted each one.

**Data flow**: It reads connection rows for the current workspace, joins owner-member information and any grant-agent names, groups repeated rows by provider and account id, sorts each connection’s agent names, and returns `ConnectionSummary` objects.

**Call relations**: Connection management views use this to show accounts as the member-owned base object, with their granted agents attached as supporting detail.

*Call graph*: 5 external calls (__init__, and_, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1281–1316)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connected accounts that the workspace’s main agent can use. Feed registration can use this to know which accounts are available by default.

**Data flow**: It reads current-workspace connections joined to grants for agents marked as main, orders them by provider and account id, converts rows into `MainAgentConnection` objects, and returns them.

**Call relations**: Feed-related code can call this when it needs accounts granted to the main agent, while ignoring accounts connected only for a shipped or specialized agent.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### Agent connector tool surface
The generic connector extension exposes searchable connector tools, inspection, execution, and safe file movement to the agent.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import/package discovery`

This file is intentionally empty. In Python, an `__init__.py` file tells Python that a folder should be treated as a package, which means its contents can be imported using normal Python import paths. Think of it like a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them reliably. Without this file, some Python environments or tooling might not recognize `extensions/connectors/ufo_ext_connectors` as an importable package. There is no runtime behavior here, no setup work, and no connector logic. Its value is structural: it helps make the connector extension area visible and organized for the rest of the project.


### `extensions/connectors/ufo_ext_connectors/tools.py`

`domain_logic` · `tool discovery and connector tool execution`

A connector broker is like a front desk for many outside services. The agent cannot carry a fixed tool for every possible GitHub, Slack, or Gmail action, so this file provides a small set of generic tools: list available connectors, discover the tools inside one connector, search for a tool by goal, and call a chosen tool. When a tool is called, the broker uses the connected account token it already holds, so this server does not inject user credentials itself.

The file also protects the workspace boundary. If an argument points to a workspace file, the file is checked, hashed, and uploaded from inside the sandbox before the broker call. If the connector returns files, they are downloaded back into a safe connector_files area in the workspace. If a provider puts base64-encoded file content directly inside JSON, this file decodes it: small readable text stays inline, while large or binary data is written to a workspace file and replaced with a reference.

There is special care for Slack sends. Messages posted through a Slack connector get a small “Sent using ufo” attribution footer, unless one is already present, because those messages bypass the normal Slack renderer that would otherwise add it. Finally, repeated large objects in connector results are replaced with same_as pointers, keeping answers smaller without throwing information away.

#### Function details

##### `list_external_tools`  (lines 239–275)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches the live connector registry for external services the agent can use, such as Slack or GitHub. It also includes already connected accounts so the caller can see which account is available before choosing a connector.

**Data flow**: It receives a tool context and keyword queries. It reads the connector registry, asks for connected accounts, matches queries against known providers and broker catalog results, then returns JSON containing matching connector IDs, labels, and usable accounts.

**Call relations**: This is one of the public connector tools exposed to the agent. It asks _registry for the current connector registry, asks _connected_accounts for account details, may search broker catalogs in parallel, and packages the answer through _json_result.

*Call graph*: calls 3 internal fn (_connected_accounts, _json_result, _registry); 1 external calls (gather).


##### `_connected_accounts`  (lines 278–292)

```
async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]
```

**Purpose**: Collects the connector accounts that this agent is already allowed to use. This helps discovery answer not just “does Slack exist?” but also “which Slack account can I act as?”

**Data flow**: It receives the tool context and reads active grants if grants are available. It groups each grant by provider and returns account IDs, owner emails, and whether each connection is shared.

**Call relations**: list_external_tools calls this while building connector search results. Its output is attached to each connector row so later steps can choose an account without another lookup.

*Call graph*: called by 1 (list_external_tools).


##### `describe_external_tools`  (lines 295–317)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes the real tools inside one connector. It can fetch exact schemas for named tools, or help discover likely tools when the caller only has a query or guessed name.

**Data flow**: It receives a source ID, optional exact tool names, and an optional search query. It looks up the connector, asks the broker for schemas, records unresolved names, optionally discovers available tools, and returns a JSON result with schemas, available tools, and unresolved names.

**Call relations**: This is the normal step before call_external_tool. It uses _registry to find the connector, _tool_json to simplify broker tool objects, _discovery_query and _discovered_rows to offer useful alternatives, and _json_result to return the final answer.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 320–324)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes any ufo attribution footer from Slack text that is being read back in. This prevents a footer written by the system from being mistaken for something a human user said.

**Data flow**: It receives a text string. It applies the attribution pattern anywhere in the text and returns the text with matching attribution fragments removed.

**Call relations**: This helper is available for inbound Slack-reading code. It is the counterpart to the outbound attribution helpers in this file.


##### `attributed_arguments`  (lines 327–354)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a Slack-style attribution footer to message arguments when the message body can safely be recognized. It avoids adding a second footer if one is already present.

**Data flow**: It receives connector arguments and the attribution subject text. It checks whether any existing argument already carries the footer, then either appends a footer block to existing Slack blocks or converts text-style arguments into blocks followed by the footer.

**Call relations**: slack_attributed calls this after deciding a connector call is a Slack message send. It relies on _carries_attribution to avoid duplicates, _appended_blocks for existing block payloads, and _body_blocks for plain text or markdown bodies.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 357–385)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns Slack text arguments into Slack message blocks so an attribution footer can be added after the body. It keeps each body in the Slack block type that matches its markup rules.

**Data flow**: It reads markdown_text or text from the argument dictionary. Markdown becomes one markdown block; plain Slack mrkdwn text becomes one or more section blocks split to Slack’s per-section size limit; if no body exists it returns nothing.

**Call relations**: attributed_arguments calls this when there is no existing blocks argument. Its output becomes the body portion before the footer block is added.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 388–409)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Adds the footer block to an existing Slack blocks value when that value is understandable and non-empty. It supports both real lists and JSON strings, including URL-encoded JSON strings.

**Data flow**: It receives a blocks value and a footer block. If the value is a list, it appends directly; if it is a string, it tries to parse it as JSON, checks for an existing footer, appends, and serializes it back in the same style; otherwise it returns nothing.

**Call relations**: attributed_arguments calls this when the original connector arguments already contain blocks. It uses _carries_attribution to avoid stacking footers and JSON/URL helpers to preserve the original format.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 412–421)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a nested value already contains a ufo attribution footer. This is the guard that prevents repeated sends or edits from accumulating duplicate footers.

**Data flow**: It receives any JSON-like value. It searches strings directly, walks lists item by item, walks dictionary values, and returns true if any nested text contains the footer pattern.

**Call relations**: attributed_arguments uses it before adding any footer, and _appended_blocks uses it after parsing serialized block data. It is a small recursive checker shared by the Slack attribution path.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 424–438)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether a connector call is a Slack message send and, if so, adds the ufo attribution footer. Non-Slack calls and Slack reads, edits, listings, or deletes are left unchanged.

**Data flow**: It receives a provider ID, tool slug, and argument dictionary. It checks for the Slack provider and for message-send-like words in the tool name, then either returns the original arguments or returns arguments changed by attributed_arguments.

**Call relations**: call_external_tool runs arguments through this before execution. If the call is the special Slack publishing case, slack_attributed hands off to attributed_arguments to do the actual footer insertion.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 441–450)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one external connector tool through its broker. It enforces read-only mode when needed, chooses the connected account, adds Slack attribution when appropriate, and starts the full execution pipeline.

**Data flow**: It receives the desired source ID, tool name, optional account ID, and tool arguments. It finds the connector, checks read-only permission if required, gets the connector connection, prepares arguments, runs _ConnectorCall, and returns the broker result as tool text.

**Call relations**: This is the public execution tool exposed to the agent. It uses _registry to find the connector, ToolContext.connector_connection to choose credentials, slack_attributed for Slack sends, and _ConnectorCall.run for staging files, executing, fetching outputs, translating data, and shrinking repeats.

*Call graph*: calls 3 internal fn (connector_connection, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 478–492)

```
async def run(self, arguments: dict[str, JsonValue], connection: ConnectorConnection) -> str
```

**Purpose**: Performs one connector tool execution from start to finish. It prepares file arguments, calls the broker, brings returned files back into the workspace, cleans up encoded data, and compresses repeated JSON objects.

**Data flow**: It receives already prepared connector arguments and a connector connection. It stages workspace-file arguments, verifies the connection, executes the broker call, fetches any output files, translates base64 content in the response, adds workspace file listings, deduplicates the result, and returns a JSON string.

**Call relations**: call_external_tool creates a _ConnectorCall and invokes this method. It coordinates _staged_value before broker execution, then _fetched_files, _translated_node, and _deduped after the broker returns.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 494–509)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through tool arguments and replaces any workspace-file marker with a broker-ready file reference. This lets connector tools receive files without the server process directly carrying the file bytes.

**Data flow**: It receives any argument value. If the value is exactly a workspace_file object, it validates the path and stages that file; if it is a dictionary or list, it recursively transforms children; otherwise it returns the value unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before executing the broker tool. When it finds a file marker, it hands the path to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 511–547)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker’s file store in a controlled way. It verifies the file, enforces size limits, asks the broker for an upload location, and uploads from inside the sandbox.

**Data flow**: It receives a workspace path chosen by the caller. It scopes the path to the workspace, computes a digest and size inside the sandbox, rejects unreadable or too-large files, asks the broker for a staged upload, optionally uses curl in the sandbox to PUT the file, and returns the broker’s argument object for that uploaded file.

**Call relations**: _staged_value calls this whenever it sees a workspace_file argument. It uses workspace path helpers, MIME type guessing, shell quoting, sandbox Python checks, and sandbox curl to keep the transfer outside the serve process.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 549–582)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It saves each file under a fresh safe path so provider-chosen names cannot overwrite or escape into other places.

**Data flow**: It receives broker file records, each with a name and presigned download URL. For each file it chooses a safe leaf name, claims a unique target path inside connector_files, downloads through sandbox curl with a size limit, and returns a list of names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker’s file_outputs view of the response. It relies on sandbox containment checks and safe-name helpers before curl writes anything.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 584–644)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Walks a result object and translates provider-marked base64 fields into readable text or workspace file references. This keeps large unreadable base64 blobs from filling the agent’s context.

**Data flow**: It receives a dictionary-like result node and a recursion depth. It first translates children, then looks for fields marked as base64, tries to decode marked content fields, chooses a name and MIME type, converts decoded bytes through _translated_bytes, and updates the node’s encoding marker when every marked field was translated.

**Call relations**: _ConnectorCall.run starts base64 translation here for the broker response, and _translated calls it for nested objects. It uses _decoded_base64 for safe decoding and _translated_bytes to decide inline text versus file offload.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 646–665)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Translates any nested result value, not just top-level objects. It recurses through dictionaries and lists and also recognizes data URLs that explicitly contain base64 data.

**Data flow**: It receives a value and its depth in the result tree. If the depth is too high it leaves the value alone; otherwise it dispatches dictionaries to _translated_node, lists to recursive item translation, data URLs to _translated_data_url, and all other values unchanged.

**Call relations**: _translated_node calls this while walking child values. It sends object children back to _translated_node and base64 data URL strings to _translated_data_url.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 667–679)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Converts a data URL containing base64 payload into readable text or a workspace file reference. A data URL is a string that embeds both a media type and bytes directly inside the text.

**Data flow**: It receives a string starting with data:. It checks whether it matches the expected base64 data URL shape, decodes the payload, derives a filename from the MIME type, and passes the bytes to _translated_bytes; invalid strings are returned unchanged.

**Call relations**: _translated calls this for suitable strings. It shares _decoded_base64 and _translated_bytes with the marked-field translation path so both forms behave consistently.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 681–690)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the final tool result. Small UTF-8 text is kept inline, while binary or large content is written to the workspace.

**Data flow**: It receives decoded bytes, optional decoded text, a suggested name, and a MIME type. If the text exists and is below the inline limit it returns the text; otherwise it calls _offloaded and returns a file reference object.

**Call relations**: _translated_node and _translated_data_url both call this after decoding base64. It is the decision point between context-friendly text and workspace-backed file storage.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 692–734)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded large or binary content into the workspace and returns a reference to it. It uses a content hash in the path so the same bytes land in the same place instead of creating duplicate files.

**Data flow**: It receives a suggested filename, MIME type, and raw bytes. It makes the filename safe, computes a SHA-256 content hash, writes bytes to a temporary part file through the sandbox, atomically places that file at the final guarded path, and returns name, workspace path, MIME type, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded content should not be kept inline. It uses sandbox writing plus a guarded placement script to avoid symlink and overwrite surprises.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 736–794)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Shrinks a connector JSON result by replacing repeated large objects with same_as pointers to their first copy. This keeps repeated boilerplate from pushing useful results out of the model’s immediate context.

**Data flow**: It receives the final payload dictionary. It serializes it once, skips deduplication if the result is too large, too structurally dense, or already contains same_as, otherwise walks the payload with _condensed and returns a JSON string of the condensed form.

**Call relations**: _ConnectorCall.run calls this at the end, in a worker thread, after file fetching and base64 translation. It uses _condensed for the structural walk and _escaped to build JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 796–872)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Recursively identifies repeated objects while preserving the first full copy. It compares structure by hashing each node, so only truly identical objects are replaced.

**Data flow**: It receives a value, its JSON Pointer path, a depth count, and a map of first-seen object hashes. It walks dictionaries, lists, and leaves, computes a digest and size for each node, records large first occurrences, and returns either the original-shaped value or a same_as pointer for repeated large objects.

**Call relations**: _deduped calls this to do the actual condensation. During recursion it uses _escaped for path pieces and SHA-256 hashes to compare provider-chosen content safely.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 875–878)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one part of a JSON Pointer path. This makes sure keys containing / or ~ still point to the exact original location.

**Data flow**: It receives a dictionary key or path token as text. It replaces ~ and / with the special JSON Pointer escape sequences and returns the escaped token.

**Call relations**: _deduped and _condensed use this while building same_as paths. Those paths let later duplicate objects point back to the first full object.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 881–905)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider explicitly marked as base64. It refuses overly large, invalid, or non-string values instead of guessing and accidentally corrupting normal text.

**Data flow**: It receives an unknown value. If it is a short enough string, it removes whitespace, strictly decodes base64, tries to decode the bytes as UTF-8 text, and returns both bytes and optional text; if any check fails it returns nothing.

**Call relations**: _translated_node calls this for marked content fields, and _translated_data_url calls it for data URL payloads. It is the gatekeeper before decoded bytes can be inlined or offloaded.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 908–922)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Searches inside one connector for tools that match a natural-language goal. It can return not only tool schemas, but also broker-provided advice such as plans, guidance, and pitfalls.

**Data flow**: It receives a source ID and query. It finds the connector, asks the broker to search, formats discovered tool rows, includes any plan/guidance/pitfall text, adds a note if discovery fell back or omitted rows, and returns JSON.

**Call relations**: This is a public discovery tool alongside describe_external_tools. It uses _registry to find the connector, _discovered_rows to apply the shared discovery fallback and budget rules, and _json_result to return the answer.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 925–928)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Retrieves the connector registry for the current turn. It fails clearly if connector tools were called without a registry being attached.

**Data flow**: It receives the tool context. It reads ctx.connectors and returns it when present, or raises an error when missing.

**Call relations**: The public connector tools call this before listing, describing, searching, or executing. It is the shared doorway into the turn’s live connector setup.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 931–932)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the plain JSON shape returned to the agent. It keeps the important fields: slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads its slug, description, and input_schema fields and returns a dictionary with those values.

**Call relations**: describe_external_tools uses this for exact schemas, and _available_tools uses it for discovery rows. It keeps public tool listings consistent.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 935–952)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the tool rows for discovery and adds a plain note when results came from a fallback. This prevents an empty search from looking like proof that the connector cannot help.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If a non-empty query found nothing, it asks for the connector’s unqueried top tools instead; then it trims rows through _available_tools and returns rows plus any explanatory note.

**Call relations**: describe_external_tools and search_connector_tools both call this, so both discovery paths follow the same “never a dead end” rule. It hands row formatting and size budgeting to _available_tools.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 955–968)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Limits a list of discovered tools to what can reasonably stay inline in the tool result. This avoids returning a huge catalog that would be pushed into a file and become harder for the agent to use.

**Data flow**: It receives broker tool listings. It converts tools to JSON rows one by one, counts their serialized size, stops once the shared budget is exceeded after at least one row, and returns the rows that fit.

**Call relations**: _discovered_rows calls this for both describe and search results. It uses _tool_json so every tool row has the same public shape.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 971–978)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Chooses the search text used when describe_external_tools needs to discover alternatives. If the caller gave no query, it turns unresolved guessed tool names into useful keywords.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present it returns it; otherwise it lowercases unresolved names, splits them into alphanumeric words, removes duplicates while keeping order, and returns the joined keywords.

**Call relations**: describe_external_tools calls this when exact tool names were missing or when discovery is requested. The resulting query is sent to the broker catalog before _discovered_rows formats the answer.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 981–982)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as the standard text-based tool result used by these connector tools. It serializes the payload to JSON so the caller receives one predictable text result.

**Data flow**: It receives a payload dictionary. It JSON-encodes that payload, places the text in a TextContent object, wraps it in a ToolResult, and returns it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools use this for their final responses. call_external_tool builds its ToolResult directly because _ConnectorCall.run already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### Composio brokered connectors
The Composio extension resolves catalog services, brokers tool discovery and execution, and performs client, proxy, and MCP transport calls without exposing user tokens.

### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package, much like putting a label on a folder so the rest of the program can find what is inside it. Without this file, depending on the Python version and packaging setup, imports that refer to `ufo_ext_composio` could fail or behave differently. There is no setup code, no exported helper, and no side effect here. Its value is structural: it gives the Composio extension a clear package boundary so other modules can refer to it by name.


### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect flow`

Composio is an outside service that can connect to many other apps and toolkits. Instead of this project listing hundreds of possible connectors one by one, this file provides an “open namespace”: if a user names a Composio toolkit by its slug, the resolver decides whether that slug is valid and connectable.

The main piece is `ComposioResolver`. It is small and mostly stateless. It keeps only a shared `ConnectorBroker`, which is the part that later runs tool actions through Composio. For each check, it asks the current Composio client for live information, so tests or runtime configuration can swap the client behavior without old connections sticking around.

The resolver does four main jobs. First, it refuses locally banned providers before asking Composio anything. Second, it asks Composio’s catalog whether a toolkit can actually be connected. Third, it builds a plain OAuth provider description for a valid slug; OAuth is the standard “sign in and grant access” flow. Here the host is blank because the user’s account token stays with Composio, and tool execution happens server-side there. Fourth, it creates a connector entry that points the slug at the shared broker.

It also exposes Composio’s allowed file-transfer hosts, so files produced or consumed by brokered tools can still pass safely through the sandbox.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio-owned hosts are allowed for file transfer. It matters because brokered tools may need to send or receive files, and the sandbox needs a safe allow-list rather than trusting any internet address.

**Data flow**: It takes no outside input beyond the resolver instance. It reads the fixed `COMPOSIO_TRANSFER_HOSTS` list from the Composio client module and returns it unchanged as a tuple of host names.

**Call relations**: When the connector system needs to know what file-store hosts a Composio-backed connection may use, it reads this property. The value is not computed from the provider slug; it applies to all Composio-brokered grants.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This async function answers the question: “Is this provider slug one that Composio can and should handle?” It first blocks locally banned names, then checks Composio’s live catalog to avoid offering a connector that cannot actually be connected.

**Data flow**: It receives a provider name as text. It lowercases the name and compares it with Composio’s banned list; if it is banned, it returns `False` immediately. Otherwise, it gets the current Composio client, asks whether the toolkit is connectable, and returns `True` only if Composio finds a matching connectable toolkit.

**Call relations**: The broader connector registry uses this during connector resolution, after explicitly registered connectors have had a chance to claim the name. If no local connector owns the slug, this resolver asks Composio via `ufo_ext_composio.client.composio_client` whether the slug belongs to its open catalog.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth description used to start connecting a Composio toolkit. OAuth is the common web flow where a user grants an app access to another service.

**Data flow**: It receives a provider slug. It creates and returns a `ComposioOAuthProvider` containing that slug and an empty host value, because this connector does not send the user directly to the provider’s own host; Composio keeps the account token and runs the tools on its side.

**Call relations**: After a provider slug has been accepted, the connect flow asks for this descriptor so it knows what kind of authorization route to use. The function hands off to `ComposioOAuthProvider.__init__` to package the slug into the standard provider description expected by the rest of the connector system.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the registry entry that says how a Composio toolkit should appear and which broker should run it. It turns a raw slug into a user-friendly label and attaches the shared Composio broker.

**Data flow**: It receives a provider slug such as `some_service`. It keeps the slug as the provider identifier, converts underscores into spaces and title-cases the result for display, then returns a new `ConnectorEntry` connected to this resolver’s broker.

**Call relations**: Once the system has decided that Composio owns a slug, it asks this function for the connector entry to use. The entry points future tool calls at the shared `ConnectorBroker`, while `ConnectorEntry.__init__` packages the provider name, display label, and broker together.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–51)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT, after: str | None=None) -> CatalogPage
```

**Purpose**: This async function searches Composio’s toolkit catalog for connectable services. It is used by discovery features so users can find services they are allowed to connect, rather than guessing slugs manually.

**Data flow**: It receives a search query, a maximum number of results, and optionally an `after` cursor, which is a marker for continuing from a previous page. It gets the current Composio client, asks it to list matching toolkits, and returns the resulting `CatalogPage`.

**Call relations**: Discovery code calls this when it wants a page of Composio-backed connector options. The function delegates the actual remote catalog lookup to the current client from `ufo_ext_composio.client.composio_client`, preserving live configuration and test overrides.

*Call graph*: 1 external calls (composio_client).


### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

This file defines `ComposioBroker`, the shared doorway used when UFO wants to work with a Composio-backed connector. Think of it like a front desk: the rest of the app asks for available tools, asks what a tool needs, runs a tool, or requests access to a connected account, and this broker translates those requests into Composio API calls.

A key detail is that the broker does not keep a long-lived client inside itself. Each method asks for the current Composio client when it runs. That matters for tests and for safe runtime behavior, because a changed transport or API setting is picked up immediately.

The file also protects users from confusing error cases. If a tool slug is wrong, it tries to return a more helpful error that includes real tool slugs for that provider. If an account connection is stale or belongs to a different broker user, it tells the agent that the member should reconnect, instead of pretending the problem is a missing tool.

For files, it supports both directions. It can find file outputs inside a nested tool response, and it can create a staged upload slot for file inputs. For credentials, it verifies ownership first and then returns a proxy-based credential, so downstream code can call the provider through Composio without ever holding the provider's real secret.

#### Function details

##### `ComposioBroker.tools`  (lines 49–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Looks up tools for a provider, optionally filtered by a search query, and returns them in UFO's standard tool shape. This is used when the system needs to show or reason about what a connector can do.

**Data flow**: It receives a workspace id, provider name, and query text. It asks the current Composio client for matching tools, then passes Composio's raw rows through `_discovered_tools` to turn them into clean `BrokerTool` objects. The result is a tuple of usable tool descriptions.

**Call relations**: When discovery is needed, this method is the broker's public entry point. It gets the live Composio client, asks Composio for the catalog rows, and hands those rows to `_discovered_tools` so the rest of UFO receives one consistent tool format.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–66)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the detailed input schema for one specific Composio tool. A schema is the recipe that tells the caller what arguments the tool accepts.

**Data flow**: It receives the workspace id, provider, and tool slug. It asks Composio for that slug's schema, rewrites any file-upload fields into UFO's workspace-file vocabulary, checks whether the tool is read-only, and returns a `BrokerTool`. If Composio says the slug does not exist, it raises `UnknownBrokerTool`.

**Call relations**: This is used after a tool has been chosen and the system needs exact calling instructions. It calls the Composio client for the raw schema, uses `workspace_file_schema` so file inputs match UFO's upload flow, and uses `_read_only` to preserve Composio's safety hint.

*Call graph*: calls 1 internal fn (_read_only); 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 68–91)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for a workspace-connected account. This is the method that turns a selected tool plus arguments into an actual action or result from Composio.

**Data flow**: It receives the workspace id, provider, tool slug, arguments, connected account id, and optional idempotency key. It builds the broker-user id from the workspace, sends the execution request to Composio, and returns Composio's response as a dictionary. If execution fails because the account is stale, it changes the error into reconnect guidance; if the slug is missing, it tries to enrich the error with available tool names.

**Call relations**: This is the main runtime path for tool calls. It uses `_stale_account` to separate dead-account problems from other failures, `_reconnect_error` to make that problem understandable, and `_slug_miss` to make missing-tool errors more useful.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 93–98)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a tool execution response. Composio can return file objects buried inside nested data, and this method extracts them into UFO's simple file format.

**Data flow**: It receives the full response dictionary from a tool call. It creates an empty list, asks `_collect_files` to recursively search the response, and returns all found files as a tuple of `BrokerFile` objects. It does not modify the original response.

**Call relations**: After `execute` returns, callers can use this method to discover downloadable file outputs. The real searching is delegated to `_collect_files`, which walks through dictionaries and lists until it finds Composio's file marker shape.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 100–116)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a place in Composio's file storage where UFO can upload a file before calling a tool. This is needed when a Composio tool expects a file input.

**Data flow**: It receives workspace and tool context plus the file name, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a `StagedUpload` containing the URL to upload to, the content type to use, and the argument object that should later be passed to the tool.

**Call relations**: This method supports the file-input side of the connector flow. The schema path rewrites uploadable parameters into UFO's workspace-file format, and this method creates the matching Composio upload target that dynamic tool execution can use.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 118–121)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio's Tool Router for tools that match a user's query. This is a higher-level search path than simply listing tools.

**Data flow**: It receives a workspace id, provider, and query. It gets the current Composio client and passes everything to `search_connector_tools`, which performs the search and returns a `BrokerSearch` result.

**Call relations**: When the system wants ranked or routed tool search, this method is the public broker hook. It does not do the search itself; it supplies the current client and context to Composio's search helper.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 123–139)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a safe credential object for provider HTTP access through Composio's proxy, after confirming the connected account belongs to this workspace's broker user. This prevents a caller from using an account it should not control.

**Data flow**: It receives a workspace id, provider, and connected account id. It builds the expected broker-user id, asks Composio to confirm the account is connected for that user and provider, and then returns a `Credential` whose transport sends requests through `ComposioProxyTransport`. If the account is not found, it raises `GrantUnusable` with reconnection guidance.

**Call relations**: This method is used when code needs provider-style HTTP access but must not receive raw provider secrets. It checks ownership with the Composio client first, then wraps access in a proxy transport, using either the client's existing transport or a default HTTP transport underneath.

*Call graph*: calls 1 internal fn (__init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, composio_client).


##### `ComposioBroker._slug_miss`  (lines 141–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Turns a plain missing-tool error into a more helpful missing-tool error when possible. If Composio cannot execute a slug, this tries to tell the caller what real slugs are available.

**Data flow**: It receives the Composio client, provider, bad slug, and original error. It turns the bad slug into search words, asks Composio for nearby tools, falls back to listing tools if needed, and returns either the original error or a new `ComposioError` whose message includes available slugs.

**Call relations**: This helper is called only by `ComposioBroker.execute` after a not-found error. It uses `_discovered_tools` to normalize candidate tools before adding their slugs to the improved error message.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Recursively searches a value for Composio file-output objects. A file-output object is recognized by having a URL, MIME type, and name in the expected shape.

**Data flow**: It receives any value and a list that is collecting results. If the value looks like a Composio file object with a non-empty `s3url`, it appends a `BrokerFile`; if the value is a dictionary or list, it searches inside each child. It returns nothing, but the provided list is filled as it searches.

**Call relations**: This is the worker behind `ComposioBroker.file_outputs`. The public method sets up the empty collection, and `_collect_files` walks the nested response like checking every drawer in a filing cabinet until it finds file records.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error means the connected account is missing or stale. This helps the system give reconnect advice instead of misleading tool-name advice.

**Data flow**: It receives a Composio error and the account id that was used. It lowercases the error text and looks for narrow signs that Composio could not find the connected account, either by the phrase "connected account" plus "not found" or by the specific account id plus "not found". It returns `true` for a likely stale account and `false` otherwise.

**Call relations**: This helper is used by `ComposioBroker.execute` before the missing-slug handling. That order matters: a dead account can sometimes surface as a not-found-style error, but the right next step is reconnection, not suggesting different tool slugs.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a clearer Composio error that tells the caller the member should reconnect the provider account. It preserves the original status while adding practical guidance.

**Data flow**: It receives the original Composio error and provider name. It combines the original error body with `stale_grant_guidance(provider)` and returns a new `ComposioError` with the same status code.

**Call relations**: This is called by `ComposioBroker.execute` after `_stale_account` identifies the failure as an account problem. It converts a low-level Composio message into guidance the agent can act on.

*Call graph*: called by 1 (execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 195–216)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio's raw tool-list rows into UFO's standard `BrokerTool` objects. It keeps the useful parts: slug, short description, input schema, and read-only hint.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it finds a usable slug, trims the description to a discovery-friendly length, rewrites input parameters with `workspace_file_schema`, checks read-only status with `_read_only`, and collects a `BrokerTool`. Rows without a valid slug are skipped, and the result is returned as a tuple.

**Call relations**: This helper is shared by `ComposioBroker.tools` for normal discovery and by `ComposioBroker._slug_miss` for helpful error messages. It is the adapter that makes Composio's catalog look like the connector interface UFO expects.

*Call graph*: calls 1 internal fn (_read_only); called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


##### `_read_only`  (lines 219–221)

```
def _read_only(payload: dict[str, object]) -> bool
```

**Purpose**: Checks whether Composio marked a tool as read-only. A read-only hint means the tool is expected to inspect or fetch data rather than change something.

**Data flow**: It receives a tool payload dictionary. It reads the `tags` field and returns `true` only if it is a list containing `readOnlyHint`; otherwise it returns `false`.

**Call relations**: This small helper is used while building `BrokerTool` objects in both `ComposioBroker.schema` and `_discovered_tools`. It keeps the read-only interpretation consistent wherever tool information enters UFO.

*Call graph*: called by 2 (schema, _discovered_tools).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector OAuth, catalog search, and connector tool execution`

Composio is used here like a connector marketplace and secure middleman. Instead of this project knowing how to call Gmail, Slack, HubSpot, and many other services directly, it asks Composio what tools exist, sends users through Composio’s OAuth consent flow, and later asks Composio to run the chosen tool. OAuth is the web sign-in and permission process; importantly, the real service token stays inside Composio, not in this project.

The file has a few main jobs. First, it decides which Composio toolkits are safe and useful to expose. Some are blocked by a hand-written ban list because their available tools or permissions are not enough for this product. Second, `ComposioClient` wraps Composio’s REST API, meaning normal HTTPS requests with JSON bodies. It can create a connection link, verify that a connected account belongs to the expected workspace user, list tools, fetch schemas, run tools, and ask for upload URLs when a tool needs a file.

It also supports Composio’s Tool Router, which is used for semantic search: the agent can ask in plain terms what it wants to do, and Composio suggests matching tool slugs, schemas, plans, guidance, and pitfalls. The file then reshapes those results into the broker types the rest of the system understands.

#### Function details

##### `connectable`  (lines 110–134)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit should be offered to users through this deploy. It checks that the toolkit is not on the project’s banned list, has Composio-managed authentication available, and actually contains tools.

**Data flow**: It receives a toolkit slug and a toolkit record from Composio. It reads the slug, authentication scheme list, and tool count, then returns `true` only if the toolkit passes all gates; otherwise it returns `false`.

**Call relations**: When the client is asked whether one toolkit can be claimed, `ComposioClient.connectable_toolkit` calls this before accepting it. When showing a page of available providers, `ComposioClient.list_toolkits` calls it to filter out unusable catalog entries.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 141–144)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception for failed or unusable Composio responses. It keeps both the HTTP status number and the response body so callers can see what went wrong.

**Data flow**: It receives a status code and response text. It builds a readable error message, stores the status and body on the exception, and produces an exception object that can be raised.

**Call relations**: Higher-level client methods raise this when Composio rejects a request or replies with missing or malformed data. `_body` also uses it as the common failure path after raw HTTP responses come back.

*Call graph*: called by 6 (_account, _auth_config, connect_link, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 161–170)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to connect an outside service account through Composio. Without this, the user could not grant permission for a connector in the normal hosted consent flow.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates the right Composio auth configuration, posts a link request to Composio, then returns the redirect URL Composio sends back; if no usable URL is present, it raises an error.

**Call relations**: This is a public client action for the OAuth start step. It relies on `_auth_config` to choose the consent configuration and `_post` to send the request, while `ComposioError.__init__` is used if Composio’s reply cannot start a browser redirect.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 172–176)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a Composio connected account is valid for this workspace user and toolkit, then returns the small account reference the rest of the system stores. It deliberately stores only the connected-account id, not any secret token.

**Data flow**: It receives an account id plus the expected user id and toolkit. It asks `_account` to verify ownership, active status, and toolkit match, then returns an `OAuthAccount` containing the account id.

**Call relations**: This is the public confirmation step after consent. It delegates the real safety checks to `_account` and hands the approved account id onward in the broker’s standard account object.

*Call graph*: calls 1 internal fn (_account); 1 external calls (__init__).


##### `ComposioClient._account`  (lines 178–207)

```
async def _account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> dict[str, object]
```

**Purpose**: Fetches and validates a connected account record from Composio. It protects against using someone else’s account, an inactive grant, or an account connected for a different toolkit.

**Data flow**: It receives an account id, expected owner id, and expected toolkit. It fetches the account JSON, checks the owner, checks that the status is `ACTIVE`, finds the toolkit slug, and either returns the account record or raises a specific error.

**Call relations**: `connected_account` calls this during account confirmation. It uses `_get` to read Composio and raises `ComposioError` for ownership or toolkit mismatches, while inactive grants become `GrantUnusable` so the user can be asked to reconnect.

*Call graph*: calls 3 internal fn (__init__, _get, __init__); called by 1 (connected_account).


##### `ComposioClient.account_label`  (lines 209–212)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a friendly label for a connected account, if Composio has one. This can help show a user which account is connected.

**Data flow**: It receives an account id, fetches that account from Composio, reads the `alias` field, and returns the alias string if it is present and non-empty; otherwise it returns `null`.

**Call relations**: This is a small read helper on top of `_get`. It does not call other higher-level logic; it simply turns Composio’s account record into an optional display label.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 214–244)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the tools available inside one toolkit, optionally narrowed by a search query. It follows Composio’s pages so tools beyond the first page are not accidentally hidden.

**Data flow**: It receives a toolkit slug and optional query. It repeatedly requests `/tools` with a page limit and cursor, collects dictionary-shaped tool rows, stops at the last page or a maximum count, and returns the collected rows as a tuple.

**Call relations**: The broker calls this when it needs to recover from or investigate a tool slug miss. Internally it uses `_get` for each page and keeps walking through Composio’s cursor-based listing until enough or all rows are gathered.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 246–247)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full schema for one Composio tool. A schema describes what inputs the tool expects and what the tool is for.

**Data flow**: It receives a tool slug, requests that tool’s record from Composio, and returns the response dictionary.

**Call relations**: This is a direct catalog lookup built on `_get`. Other parts of the connector system can use it when they need exact details for a known tool.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 249–266)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a user-supplied toolkit slug names a toolkit this deploy is willing and able to broker. It also rejects suspicious slugs before they are put into a URL path.

**Data flow**: It receives a slug string. It first checks that the slug contains only normal toolkit characters, fetches the toolkit record if so, treats a 404 as not found, applies `connectable`, and returns the toolkit’s display name or slug; if any check fails it returns `null`.

**Call relations**: This is used when deciding whether the resolver can claim a provider name. It depends on `_get` for the Composio lookup and on `connectable` for the project’s safety and usefulness rules.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 268–295)

```
async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads one page of Composio toolkits that are connectable through this deploy. This powers a provider catalog rather than exposing Composio’s raw list unchanged.

**Data flow**: It receives a search query, page size, and optional cursor. It requests the toolkit page, filters each item through `connectable`, turns accepted rows into `CatalogEntry` objects, and returns a `CatalogPage` with entries and the next cursor if there is one.

**Call relations**: This is the catalog browsing path. It uses `_get` to read Composio, `connectable` to remove bad or empty providers, and then hands back broker-facing catalog objects.

*Call graph*: calls 2 internal fn (_get, connectable); 2 external calls (__init__, __init__).


##### `ComposioClient.execute_tool`  (lines 297–311)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio’s server side for a broker user and, when supplied, a specific connected account. This is where an outside-service action actually happens.

**Data flow**: It receives a tool slug, argument values, user id, optional connected account id, and optional idempotency key. It builds the JSON request body, checks that it is not larger than the allowed size, adds an idempotency header if provided, posts to Composio, and returns Composio’s response dictionary.

**Call relations**: Execution goes straight through Composio’s execute API, not through Tool Router search. The method uses `_post` for the network call and `json.dumps` to measure the outgoing payload before sending.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 313–337)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file that a tool will later use. This lets the sandbox upload bytes directly to Composio’s file store rather than sending file contents through this client.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 hash. It posts an upload request, validates that Composio returned a storage key, accepts either a new presigned PUT URL or a deduplicated existing file, and returns a `ComposioUpload` with the key and optional upload URL.

**Call relations**: Tool execution that needs files can call this before building the final tool arguments. It relies on `_post` for the Composio request and raises `ComposioError` if the upload slot response is not usable.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 339–350)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The session provides an MCP endpoint, meaning a tool-calling endpoint used to ask Composio’s router for matching tools.

**Data flow**: It receives a user id and a list of toolkit slugs. It posts a session request, reads the returned session id and MCP URL, validates both, and returns a `ToolRouterSession` object.

**Call relations**: `search_connector_tools` calls this when no cached session exists for a user and connector. The method uses `_post` and raises `ComposioError` if Composio does not return the session details needed for later search calls.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 352–370)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the Composio authentication configuration that should be used for a toolkit’s consent flow. It prefers an existing project configuration, but creates a Composio-managed one if none exists.

**Data flow**: It receives a toolkit slug. It asks Composio for existing auth configs, extracts the first id if present, otherwise posts a request to create a managed auth config, validates the created id, and returns that id.

**Call relations**: `connect_link` calls this before minting a user-facing OAuth link. It uses `_get`, `_post`, and `_auth_config_id`, with `ComposioError` as the failure path when Composio does not provide an id.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 372–374)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a GET request to Composio and converts the response into a dictionary. GET is the normal web method for reading data.

**Data flow**: It receives a path and optional query parameters. It opens an HTTP client, sends the GET request, passes the raw response to `_body`, and returns the parsed dictionary.

**Call relations**: Most read methods in `ComposioClient` go through this helper, including account lookup, catalog lookup, toolkit listing, and auth config lookup. It gets the configured HTTP client from `_http` and leaves response validation to `_body`.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_account, _auth_config, account_label, connectable_toolkit, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 376–380)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a POST request to Composio and converts the response into a dictionary. POST is the normal web method for creating something or asking a service to perform an action.

**Data flow**: It receives a path, a JSON body, and optional headers. It opens an HTTP client, sends the POST request, passes the raw response to `_body`, and returns the parsed dictionary.

**Call relations**: Action methods such as creating links, executing tools, creating uploads, creating auth configs, and opening router sessions all use this helper. Like `_get`, it uses `_http` for the client and `_body` for response checking.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 382–388)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the configured asynchronous HTTP client used for one Composio request. Asynchronous means the program can wait for the network without blocking other work.

**Data flow**: It reads the client’s API key and optional test transport, then returns an `httpx.AsyncClient` configured with Composio’s base URL, API key header, timeout, and transport.

**Call relations**: `_get` and `_post` call this whenever they need to talk to Composio. By creating the HTTP client here, all requests share the same base settings while still allowing tests to swap in a fake transport.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 391–399)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Composio into a usable dictionary or a clear error. It prevents callers from silently accepting failed requests or unexpected response shapes.

**Data flow**: It receives a raw HTTP response. If the status code is an error, it raises `ComposioError`; if the body is empty, it returns an empty dictionary; otherwise it parses JSON and confirms the JSON is an object before returning it.

**Call relations**: Both `_get` and `_post` call this after every Composio request. It is the shared checkpoint that separates successful dictionary responses from failures.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 402–428)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the project’s simpler workspace-file format. This lets the agent name a file path in `/workspace` instead of dealing with Composio’s internal file-store fields.

**Data flow**: It receives any schema value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object requiring a `workspace_file` path; for other dictionaries and lists, it recursively rewrites their contents; other values pass through unchanged.

**Call relations**: `_search_result` calls this while converting Tool Router results into broker tools. It ensures the tools shown to the agent use the project’s file vocabulary before the agent tries to call them.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 431–438)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small helper for the consent setup path.

**Data flow**: It receives a response dictionary. It looks for an `items` list, scans for the first dictionary item with a string `id`, and returns that id; if none is found, it returns `null`.

**Call relations**: `ComposioClient._auth_config` calls this after listing existing auth configs. If this helper returns an id, the client reuses it instead of creating a new auth config.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 441–448)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the normal Composio client for this deploy using the API key from the environment. It fails loudly if the key is missing because connector OAuth cannot work without it.

**Data flow**: It reads `COMPOSIO_API_KEY` from environment variables. If present, it returns a `ComposioClient` built with that key; if absent, it raises a runtime error.

**Call relations**: Other setup or request paths can call this when they need the default production client. It does not make a network call itself; it only prepares the client object with the deploy’s broker key.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 455–456)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This avoids crashes when Composio or Tool Router returns missing or oddly shaped data.

**Data flow**: It receives any value. If the value is a dictionary, it returns that dictionary; otherwise it returns an empty dictionary.

**Call relations**: `_search_result` uses this repeatedly while walking nested Tool Router data. It keeps the conversion code simple and defensive.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 459–462)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. This filters out missing, blank, or non-string values.

**Data flow**: It receives any value. If the value is a list, it keeps only non-empty strings and returns them as a tuple; otherwise it returns an empty tuple.

**Call relations**: `_search_result` uses this to collect tool slugs, plan steps, guidance, and pitfalls from Tool Router results without trusting every field blindly.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 465–499)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router’s raw search answer into the broker search format the dynamic connector tools display. It gathers matching tools plus useful advice for how to use them.

**Data flow**: It receives a result dictionary. It finds the nested data and schemas, walks each search result, collects primary and related tool slugs without duplicates, rewrites file schemas into workspace-file form, and returns a `BrokerSearch` containing tools, plan steps, guidance, and pitfalls.

**Call relations**: `search_connector_tools` calls this after the MCP search request completes. The helper uses `_dict`, `_str_tuple`, and `workspace_file_schema` to safely reshape the raw router response into broker-facing `BrokerTool` and `BrokerSearch` objects.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 502–525)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Runs semantic tool discovery for one connector. In plain terms, it asks Composio’s Tool Router, “Which tools should I use for this user request?” and returns structured suggestions.

**Data flow**: It receives a Composio client, workspace id, connector slug, and search query. It builds the broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the router’s search tool over MCP with the query, and converts the result with `_search_result`.

**Call relations**: This is the high-level search flow for dynamic connector tools. It calls `ComposioClient.tool_router_session` only when a cached session is missing, then hands the live router URL to `mcp_session.mcp_call_tool`, and finally passes the raw answer to `_search_result`.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Some connected services, like Google or Slack, need private credentials to fetch data. In this project, Composio keeps those credentials on its own side, so this code acts like a mail-forwarding office: callers write a normal request to the provider, but the transport forwards it to Composio, which adds the hidden credential and sends it on.

The main class, `ComposioProxyTransport`, plugs into `httpx`, an HTTP client library. When a connector makes a request, this transport reads the method, full URL, safe headers, and body. It packages them into a JSON request to Composio’s `/tools/execute/proxy` endpoint, along with the connected account id. It deliberately removes headers such as `Authorization`, `Host`, and body-size headers because those either belong to the original connection or could be unsafe or wrong after rewriting the request.

When Composio replies, the file rebuilds the provider’s response so the rest of the connector can behave as if it had talked directly to the provider. That matters for things like pagination, where the next page may be named in response headers. If the provider response is binary, Composio stores the bytes elsewhere and returns a temporary download URL; this transport turns that into a redirect response rather than pulling large files through the event loop.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 61–97)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main entry point for each outgoing provider request. It rewrites that request into a Composio proxy call so Composio can attach the secret credential server-side.

**Data flow**: It receives an `httpx.Request` that was originally aimed at the provider. It reads the request body, copies the method and full URL, keeps only headers that are safe to forward, and includes the connected account id. If there is a body, it sends JSON bodies as JSON and other text bodies as text. It then creates a new POST request to Composio’s proxy endpoint using the Composio API key. The response either comes back as an error response directly from Composio, or is passed onward to be rebuilt as a provider-style response.

**Call relations**: This function is called by `httpx` whenever the bound client sends a request through this transport. After Composio answers, it hands successful proxy payloads to `ComposioProxyTransport._provider_response`, which converts Composio’s wrapper format back into the response shape the connector expects.

*Call graph*: calls 1 internal fn (_provider_response); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._provider_response`  (lines 99–142)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This function unwraps Composio’s proxy result and rebuilds the HTTP response that the original provider would have returned. It keeps useful status codes, headers, and body content so ordinary connector code can keep working.

**Data flow**: It receives the decoded JSON payload from Composio plus the original request. It peels away nested `data` envelopes until it reaches the provider result, reads the status code, filters out body-related headers that should not be reused, and then builds response bytes from the returned data. If Composio reports binary data, it checks for a temporary download URL and returns a 302 redirect with that URL in the `Location` header. If that URL is missing, it raises a Composio-specific error because the caller has no way to fetch the file.

**Call relations**: It is used by `ComposioProxyTransport.handle_async_request` after a successful proxy call. Its output goes back to the original HTTP caller as though the caller had received it directly from the provider, including JSON bodies, text bodies, headers, redirects, and status codes.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 144–145)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport when the proxy transport is no longer needed. It makes sure network resources are released cleanly.

**Data flow**: It takes no new data from the caller. It asks the wrapped inner transport to close itself, which shuts down any open connections or related resources. It returns nothing after cleanup finishes.

**Call relations**: This is part of the normal `httpx` transport lifecycle. When the client using `ComposioProxyTransport` is closed, this method passes the close request down to the inner transport that actually made the Composio network calls.


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio's Tool Router. The Tool Router exposes tool search through an MCP endpoint. MCP, or Model Context Protocol, is a standard way for software to offer tools to an AI system. Here, the code opens a streamable HTTP connection, calls one tool, closes the connection, and then cleans up the response into a simple dictionary.

The important idea is that this file is only for searching tools through the router. Actual tool execution happens elsewhere through Composio's execute API, so billing, permissions, and grants stay tied to the correct Composio path.

The response can come back in several shapes. The file first looks for already-parsed dictionary data. If that is not available, it checks for structured content. If that is also missing, it looks at text blocks and tries to read the first one as JSON. As a fallback, it wraps plain text or other data in a dictionary. This is like receiving a package that might already be labeled, might have a packing slip inside, or might only have a handwritten note; the function tries each sensible place before giving up.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one named tool on a Composio MCP endpoint and returns the result as a plain dictionary. It is used when the project needs a clean, predictable answer from a remote Tool Router call.

**Data flow**: It receives an endpoint URL, a tool name, a dictionary of tool arguments, HTTP headers, and a timeout. It opens a FastMCP streamable HTTP client, sends the tool call with those arguments, then closes the session. After the remote call returns, it looks for the best usable result: first parsed dictionary data, then structured dictionary content, then JSON found inside a text block, and finally a simple wrapper around any leftover value.

**Call relations**: When higher-level Composio code needs to query the Tool Router, it calls this function as the transport step. Inside, this function creates a StreamableHttpTransport to reach the endpoint, uses fastmcp.Client to perform the actual MCP tool call, and uses json.loads only if it must turn a text response into structured data.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### Pipedream brokered connectors
The Pipedream extension declares its package, brokers action discovery and execution, and uses a direct client with connected-account safety checks.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a directory can act like a named package when it contains an `__init__.py` file. That means other code can import things from `ufo_ext_pipedream`, much like opening a labeled folder and finding the tools inside it. Because this file has no code, it does not set up configuration, start services, or expose helper functions directly. Its value is structural: without it, some Python tooling or older import systems might not recognize this directory as a package, which could make imports for the Pipedream extension fail or behave inconsistently. In short, this file is a signpost. It tells Python and readers that the surrounding directory belongs together as the `ufo_ext_pipedream` package.


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`orchestration` · `request handling`

Pipedream offers many app actions, such as sending an email or creating a ticket. This file is the adapter that makes those actions look like normal UFO connector tools. Think of it as a hotel front desk: callers ask for available services, provide details, and the desk makes sure the right room key, account, and outside service are used.

The broker is intentionally stateless. Each method asks for a fresh Pipedream client when it runs, so tests can swap the network layer and live calls do not keep old connections or credentials around.

For discovery, it lists Pipedream actions for a provider and turns them into `BrokerTool` objects. It builds a JSON schema, which is a machine-readable description of allowed inputs, from Pipedream's configurable properties. It hides internal fields, especially the app account slot, because the broker fills that in itself.

For execution, it fetches the action definition, checks that the supplied account belongs to the current workspace and app, inserts the account into the action arguments, and calls Pipedream's run API. If the action name is wrong, it returns a helpful error with close matches. If an old connected account can no longer be used, it tells the agent to reconnect.

The file also turns Pipedream file-stash outputs into downloadable file links. It refuses staged uploads because Pipedream actions expect file inputs as URLs instead.

#### Function details

##### `PipedreamBroker.tools`  (lines 62–64)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds actions for one Pipedream app and presents them as broker tools the rest of the system can understand. Someone uses this when they want to search or list what a connected app can do.

**Data flow**: It receives a workspace id, a provider name, and a search query. It looks up the provider's Pipedream app slug, asks the Pipedream client for matching actions, then converts the returned action rows into `BrokerTool` records. The result is a tuple of available tools.

**Call relations**: This is the main discovery path. `PipedreamBroker.search` calls it when a broader broker search is requested, and it relies on `_spec` to translate the provider name and `_listed_tools` to reshape Pipedream's raw catalog rows.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 66–73)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the detailed input description for one Pipedream action. This helps a caller know what arguments an action accepts before trying to run it.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts the configurable properties, turns those properties into a JSON schema, reads the description and read-only hint, and returns a `BrokerTool` describing that action.

**Call relations**: This is used when the system already has an action key and needs its shape. It calls `_definition` for the raw Pipedream data, then uses `_props`, `_input_schema`, `_read_only`, and `_str` to turn that data into a clean broker-facing tool description.

*Call graph*: calls 5 internal fn (_definition, _input_schema, _props, _read_only, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 75–110)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action on behalf of a workspace using a specific connected account. It also turns several confusing failure cases into clearer errors, such as a misspelled action key or a stale account that needs reconnecting.

**Data flow**: It receives the workspace id, provider, action slug, user-supplied arguments, connected account id, and an optional idempotency key. It fetches the action definition, copies the arguments, inserts the account into the hidden app slot, verifies the account belongs to the requested app, and sends the action to Pipedream's run API. It returns Pipedream's response, unless Pipedream reports an action error or stale account problem, in which case it raises a clearer exception.

**Call relations**: This is the main run path. It calls `_definition` first; if the action is unknown, it asks `_key_miss` to build a helpful not-found error. It uses `_app_slot` to know where to bind the account, `_spec` to check the correct app, and `_stale_account` plus `_reconnect_error` to guide the caller when an old grant can no longer be used.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 112–130)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files that a Pipedream action saved and exposes them as downloadable broker files. This lets the sandbox or caller fetch action-created files without needing to understand Pipedream's internal file-stash format.

**Data flow**: It receives a Pipedream action response. It looks inside the response's exports for the special file-stash uploads list, keeps only entries with a usable download URL, derives a filename from the local path when available, and returns `BrokerFile` objects. It does not change the response.

**Call relations**: This is used after an action has run. It does not call other broker logic; it simply translates Pipedream's `$filestash_uploads` export into the standard file format expected by the rest of the connector system.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 132–144)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged file uploads for Pipedream actions. Pipedream expects file inputs to be URLs, so the correct path is to share a workspace file and pass its download link.

**Data flow**: It receives details for a file that someone wants to stage, such as filename, MIME type, and checksum. Instead of creating an upload target, it immediately raises an error explaining that the caller should use a shared file URL. Nothing is uploaded or returned normally.

**Call relations**: This is the broker's guardrail for file inputs. It stands apart from the normal run flow and prevents callers from using an upload pattern that Pipedream actions do not support.


##### `PipedreamBroker.search`  (lines 146–147)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps Pipedream action discovery in the standard broker search result format. Pipedream does not provide a separate planning router here, so the result is just matching tools.

**Data flow**: It receives a workspace id, provider, and query. It asks `PipedreamBroker.tools` for matching actions, then places those tools into a `BrokerSearch` object. The output is a search result containing the available tools.

**Call relations**: This is a thin wrapper around `PipedreamBroker.tools`. When the wider system asks to search external tools, this method delegates the actual Pipedream lookup to `tools` and packages the answer in the expected search container.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 149–170)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can send proxied HTTP requests through Pipedream for a connected account. It first checks that the account belongs to the workspace and authenticates the expected app.

**Data flow**: It receives a workspace id, provider, and account id. It looks up the provider's Pipedream app, fetches the connected account from Pipedream, turns a missing account into reconnect guidance, rejects accounts for the wrong app, and returns a `Credential` containing a `PipedreamProxyTransport`. That transport carries the account information needed for later proxied requests.

**Call relations**: This is used when the system needs authenticated transport, not just action execution. It uses `_spec` to know which app is expected, the Pipedream client to verify the account, `PipedreamProxyTransport` to build the proxy layer, and reconnect guidance when the grant is stale or missing.

*Call graph*: calls 3 internal fn (__init__, _spec, __init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, pipedream_client).


##### `PipedreamBroker._definition`  (lines 172–180)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for one action slug. It turns Pipedream's not-found response into the broker's standard unknown-tool error.

**Data flow**: It receives an action slug. It asks the Pipedream client for the action definition, unwraps the `data` field when that is where the definition lives, and returns a dictionary. If Pipedream says the action does not exist, it raises `UnknownBrokerTool` instead.

**Call relations**: `PipedreamBroker.schema` uses this to describe an action, and `PipedreamBroker.execute` uses it before running one. It is the shared doorway from a broker action key to Pipedream's raw definition data.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 182–201)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when someone tries to run an action slug that Pipedream does not recognize. Instead of only saying 'not found', it suggests close real action keys when possible.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider's app, tries to list all actions for that app, compares the missing slug with real tool slugs, and returns a `PipedreamError` containing either close matches or advice to search the catalog. If listing actions also fails, it still returns a useful generic hint.

**Call relations**: `PipedreamBroker.execute` calls this after `_definition` reports an unknown action. This function uses `_spec` to find the app, `_listed_tools` to normalize catalog rows, and close-match logic to make the next model or user attempt less blind.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute); 1 external calls (get_close_matches).


##### `_stale_account`  (lines 204–211)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream error probably means the connected account is no longer usable. This matters because the system can then tell the user to reconnect instead of showing a vague provider error.

**Data flow**: It receives a `PipedreamError` and the account id that was being used. It lowercases the error body and checks for narrow signs such as 'external user not found' or the account id appearing with 'not found'. It returns `true` only for those stale-account-looking errors, otherwise `false`.

**Call relations**: `PipedreamBroker.execute` uses this after run failures and action-level errors. When it returns true, `execute` passes the error to `_reconnect_error` so the caller gets reconnect guidance.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 214–215)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds reconnect instructions to a Pipedream error. It keeps the original status code but appends guidance that tells the agent or user how to repair a stale grant.

**Data flow**: It receives a Pipedream error and provider name. It combines the original error body with `stale_grant_guidance` for that provider and returns a new `PipedreamError` carrying the combined message.

**Call relations**: `PipedreamBroker.execute` calls this when `_stale_account` says a failed run likely came from an old or missing account connection. It is the final step that turns a low-level failure into actionable advice.

*Call graph*: calls 1 internal fn (__init__); called by 1 (execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 218–222)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the Pipedream connector specification for a provider name. The specification tells this broker which Pipedream app slug the provider represents.

**Data flow**: It receives a provider string. It checks Pipedream's connector registry and returns the matching `ConnectorSpec`. If there is no registered provider by that name, it raises a key error with a clear message.

**Call relations**: Several broker paths depend on this translation: `tools` needs it for catalog listing, `execute` and `credential` need it to verify the connected app, and `_key_miss` needs it to search the right action catalog.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 225–242)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw Pipedream action-list rows into standard broker tool descriptions. It filters out invalid rows and keeps the action key, description, input schema, and read-only hint.

**Data flow**: It receives a tuple of dictionaries from Pipedream's action list. For each row with a non-empty string key, it extracts configurable properties, builds an input schema, reads the description and read-only marker, and creates a `BrokerTool`. It returns all valid tools as a tuple.

**Call relations**: `PipedreamBroker.tools` uses this for normal discovery, and `_key_miss` uses it when building suggestions for a missing action slug. It delegates smaller cleanup steps to `_props`, `_input_schema`, `_read_only`, and `_str`.

*Call graph*: calls 4 internal fn (_input_schema, _props, _read_only, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_read_only`  (lines 245–247)

```
def _read_only(definition: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Pipedream action declares itself read-only. A read-only action is expected to look up information rather than change outside state.

**Data flow**: It receives an action definition dictionary. It looks for an `annotations` dictionary and checks whether `readOnlyHint` is exactly true. It returns a boolean.

**Call relations**: `PipedreamBroker.schema` and `_listed_tools` call this when creating `BrokerTool` objects. It supplies one small but important safety signal about what kind of effect an action may have.

*Call graph*: called by 2 (schema, _listed_tools).


##### `_props`  (lines 250–252)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the list of configurable property dictionaries from a Pipedream action definition. These properties are the raw material for deciding what inputs the caller may provide.

**Data flow**: It receives an action definition dictionary. It reads `configurable_props`, keeps it only if it is a list, filters that list down to dictionary entries, and returns the cleaned list. Missing or malformed data becomes an empty list.

**Call relations**: `PipedreamBroker.schema`, `_listed_tools`, and `_app_slot` all call this before inspecting action inputs. It centralizes the cleanup so the rest of the broker can work with a predictable list.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 255–262)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the hidden action input where the connected account must be placed. Without this slot, the broker cannot run the action as the user's granted account.

**Data flow**: It receives an action definition and the action slug. It scans the cleaned configurable properties for one whose type is Pipedream's app/account property type and whose name is a usable string. It returns that property name, or raises a `PipedreamError` if no account slot exists.

**Call relations**: `PipedreamBroker.execute` calls this just before running an action. The returned slot name tells `execute` where to insert the account binding in the arguments sent to Pipedream.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 265–288)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds a JSON schema for the action inputs that a caller is allowed to set. It hides Pipedream-internal fields and the account slot, because those are not user-facing arguments.

**Data flow**: It receives cleaned configurable properties. It skips properties without names, internal app or directory properties, and service properties whose types start with `$.`. For each remaining property, it maps Pipedream's type to a JSON type, adds a description when available, and marks non-optional properties as required. It returns a schema dictionary with `type`, `properties`, and sometimes `required`.

**Call relations**: `PipedreamBroker.schema` uses this for a single detailed action description, and `_listed_tools` uses it while presenting catalog results. It calls `_str` to safely read property types.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 291–292)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. This prevents accidental display of non-string data as descriptions or types.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It has no side effects.

**Call relations**: `PipedreamBroker.schema`, `_listed_tools`, and `_input_schema` use this as a small safety helper when reading loose dictionaries from Pipedream. It keeps malformed or unexpected fields from leaking into broker-facing descriptions and schemas.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector setup and connector action execution`

This file is the bridge between UFO and Pipedream’s hosted connector service. Pipedream does three important jobs for the project: it shows the user a consent page, stores and refreshes the user’s app credentials, and runs prebuilt app actions on the server side. This code wraps those remote API calls in one Python client so the rest of the extension does not need to know Pipedream’s HTTP details.

The file also contains an explicit list of supported connectors. Each connector says its human name, its Pipedream app name, and the outside API host that a grant may allow. Some connectors use this deployment’s own OAuth app, which means the user grants access through the project’s registered app rather than Pipedream’s shared app.

A major theme is ownership checking. The project-level Pipedream token can read many accounts, so the client must prove that an account belongs to the current workspace or connection flow before using it. Think of it like a hotel master key: powerful, but every door opening still needs a room assignment check. If an account is unhealthy, missing, owned by someone else, or shaped differently than expected, the file fails loudly instead of pretending the grant is usable.

#### Function details

##### `PipedreamError.__init__`  (lines 120–123)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Builds a clear exception for a failed or unusable Pipedream response. It keeps both the HTTP status number and the response text so callers can report what went wrong instead of losing the original evidence.

**Data flow**: It receives a status code and response body text → turns them into a readable error message like “pipedream 403: ...” → stores the status and body on the error object for later inspection.

**Call relations**: This is the shared failure path for this client and for nearby broker code. Whenever a Pipedream response is bad, incomplete, forbidden, or unsafe to use, callers raise this error to stop the connector flow immediately.

*Call graph*: called by 13 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, account_token, connect_token, newest_account, workspace_account (+3 more)).


##### `PipedreamClient.access_token`  (lines 161–182)

```
async def access_token(self) -> str
```

**Purpose**: Gets the short-lived Pipedream access token that authenticates this deployment when calling Pipedream’s API. It reuses a cached token until it is close to expiring, avoiding an extra login request for every API call.

**Data flow**: It reads the client id from the object and checks the process-wide token cache → if a still-valid token exists, it returns it → otherwise it posts the client id and secret to Pipedream’s OAuth endpoint, validates the response, stores the new token with its expiry time, and returns the token string.

**Call relations**: All authenticated GET and POST helpers call this first. It uses the raw HTTP client because the token request itself is the one Pipedream call that cannot already carry a bearer token.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 184–199)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a Pipedream Connect token and hosted consent link for a user. This is what lets the system send the user to Pipedream’s web page to approve access to an outside app.

**Data flow**: It receives an external user id plus success and error return URLs → posts those values to Pipedream → checks that the response contains both a token and a browser link → returns them as a ConnectToken object.

**Call relations**: Connector setup flows call this when they need to start a new OAuth consent journey. It delegates the actual HTTP POST to the client’s authenticated helper and raises PipedreamError if Pipedream does not return the link needed to continue.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 201–208)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and confirms that it belongs to the expected external user. This prevents a caller from supplying someone else’s account id and accidentally getting access through the project-wide Pipedream token.

**Data flow**: It receives an account id and expected external user id → fetches the account record from Pipedream → unwraps the record if it is nested under a data field → checks ownership and health → returns a ConnectedAccount summary.

**Call relations**: This is used when the flow already knows which external user should own the account. It hands the safety decision to _owned_account, which in turn uses _account to reject broken or unhealthy records.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 210–214)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Fetches a friendly display name for a connected account, if Pipedream provides one. This is useful for showing a user which account they connected without exposing credentials.

**Data flow**: It receives an account id → fetches the account record from Pipedream → looks for a non-empty name field → returns that string, or None if no useful name exists.

**Call relations**: This is a read-only convenience method. It uses the same authenticated GET helper as the stricter account methods, but it does not perform the full ownership projection because it only returns a label.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 216–226)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads a connected account and confirms that it belongs to the given workspace. It is a guardrail used before allowing a workspace to execute through a Pipedream account.

**Data flow**: It receives an account id and workspace UUID → fetches the account record → converts the record into a ConnectedAccount, rejecting unhealthy or malformed records → checks that the account’s external user id matches the workspace’s allowed id pattern → returns the account or raises an error.

**Call relations**: This method is part of the access-control story around connector execution. It relies on _account to understand Pipedream’s account record and _workspace_owns_external_user to decide whether the workspace is allowed to use it.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.account_token`  (lines 228–252)

```
async def account_token(self, account_id: str, workspace_id: UUID) -> str
```

**Purpose**: Retrieves the provider access token behind a connected account, but only after confirming that the account belongs to the workspace. This is used for special cases like GitHub command-line tools, where the sandbox needs a real provider token routed safely rather than a normal proxied action.

**Data flow**: It receives an account id and workspace UUID → asks Pipedream for the account with credentials included → validates the account and workspace ownership → extracts oauth_access_token from the credentials block → returns the token string or raises if no token is available.

**Call relations**: This is one of the most sensitive methods in the file because it can obtain a secret. It therefore repeats the workspace ownership check before reading the token and uses PipedreamError or GrantUnusable-style account validation to stop unsafe or unusable cases.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 254–268)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for a particular external user and app. This is used after a hosted consent flow returns, when the system needs to discover which account the user just connected.

**Data flow**: It receives an external user id and Pipedream app name → lists matching accounts from Pipedream → chooses the record with the latest created_at value → verifies it has an id and belongs to that same external user → returns a ConnectedAccount.

**Call relations**: OAuth return handling calls this kind of lookup after Pipedream finishes consent. It uses _owned_account so a newly found account is still checked for ownership and health before being accepted.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 270–299)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists Pipedream’s available actions for an app, optionally filtered by a search query. It follows pages of results so discovery can find actions beyond the first page.

**Data flow**: It receives an app name and optional query → repeatedly fetches action pages from Pipedream with a page limit and cursor → collects dictionary-shaped action rows → stops when the page is short, no cursor exists, or the configured maximum is reached → returns the collected rows as a tuple.

**Call relations**: The broker calls this when it cannot directly match a requested action key and needs to search Pipedream’s catalog. It uses the shared GET helper for each page and _dict to safely read pagination details.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 301–302)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition for one Pipedream component or action key. Callers use this when they need to understand the action’s inputs and shape before running it.

**Data flow**: It receives a component key → performs an authenticated GET to Pipedream’s component endpoint → returns the response dictionary.

**Call relations**: This is a thin catalog lookup around _get. It fits between action discovery and action execution: once code knows a key, this method can retrieve the full description for that key.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 304–322)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action on the server side for a connected external user. It also asks Pipedream to use a fresh file stash so files created by the action can be returned as downloadable links instead of disappearing inside Pipedream’s temporary container.

**Data flow**: It receives an action key, external user id, and configured input properties → builds the action-run request body with a new stash id → checks that the JSON body is not larger than the configured limit → posts it to Pipedream → returns Pipedream’s response dictionary.

**Call relations**: Connector execution paths call this after they have chosen an action and prepared its inputs. It hands the network work to _post and protects Pipedream from oversized payloads before sending anything.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 324–327)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked response body. It centralizes the repeated steps of getting a bearer token, opening an HTTP client, and parsing errors.

**Data flow**: It receives a path and optional query parameters → obtains an access token → opens an HTTP client with that token → sends the GET request → passes the response through _body → returns the parsed dictionary.

**Call relations**: Most read methods in this file call _get, including account reads, action listing, and action definition lookup. It relies on access_token for authentication and _http for the actual client setup.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 7 (account_label, account_token, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 329–332)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked response body. It keeps write-style calls consistent and makes error handling the same everywhere.

**Data flow**: It receives a path and JSON body dictionary → obtains an access token → opens an HTTP client with that token → posts the JSON body → validates and parses the response through _body → returns the parsed dictionary.

**Call relations**: connect_token and run_action use this helper for their Pipedream POST calls. Like _get, it is the small transport layer that hides token lookup and response checking from higher-level methods.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 334–347)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates an asynchronous HTTP client pointed at Pipedream’s API. For authenticated calls, it adds both the bearer token and the Pipedream environment header.

**Data flow**: It receives an optional token → if a token is present, builds authorization and environment headers → creates an httpx AsyncClient with the base URL, timeout, optional test transport, and headers → returns that client for use in an async context.

**Call relations**: access_token, _get, and _post all call this when they need to talk to Pipedream. The token request calls it without headers, while normal API calls call it with a token.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 350–351)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This avoids crashes or awkward type checks when Pipedream returns optional nested fields.

**Data flow**: It receives any value → if the value is a dictionary, returns it unchanged → otherwise returns an empty dictionary.

**Call relations**: Account and action-reading code use this helper whenever a response field may or may not be an object. It is a small defensive tool used before reading nested keys.

*Call graph*: called by 7 (account_label, account_token, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 354–367)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Confirms that a Pipedream account record belongs to one exact external user. It is the narrow ownership check used when the caller knows the expected external user id.

**Data flow**: It receives a raw account record, account id, and expected external user id → converts the record into a ConnectedAccount with _account → compares the account’s owner to the expected owner → returns the account if they match or raises PipedreamError if they do not.

**Call relations**: connected_account and newest_account call this after fetching account records. It builds on _account’s shape and health checks, then adds the specific owner check needed for consent correlation.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 370–393)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into the small ConnectedAccount object this code trusts. It refuses records that have no owner or are marked unhealthy, because those accounts cannot safely authenticate.

**Data flow**: It receives a raw account record and account id → reads the external owner id → raises an error if the owner is missing → raises GrantUnusable if Pipedream says the account is unhealthy → reads the app slug if present → returns a ConnectedAccount with the id, app, and owner.

**Call relations**: This is the common account validator used by workspace_account, account_token, and _owned_account. It separates “Pipedream/API problem” from “the user must reconnect this grant” by raising GrantUnusable for unhealthy accounts.

*Call graph*: calls 3 internal fn (__init__, __init__, _dict); called by 3 (account_token, workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 396–397)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used for Pipedream external user ids that belong to a workspace. This gives the rest of the file one consistent naming rule.

**Data flow**: It receives a workspace UUID → uses its compact hexadecimal form → returns a string beginning with the project’s external-user prefix and ending with an underscore.

**Call relations**: _workspace_owns_external_user uses this to recognize workspace-scoped users, and connection_user_id uses it when creating a new connection-specific external user id.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 400–409)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Checks whether a Pipedream external user id is valid for a given workspace. This is the workspace-level ownership test that stops a workspace from using an account connected under another workspace.

**Data flow**: It receives a workspace UUID and an external user id → accepts an older direct workspace id form if it matches → otherwise checks for the workspace prefix → then verifies the remaining connection id is exactly 32 lowercase hexadecimal characters → returns true or false.

**Call relations**: workspace_account and account_token call this before allowing account use. It depends on workspace_user_prefix so the check matches the same format used when new connection user ids are created.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 2 (account_token, workspace_account).


##### `connection_user_id`  (lines 412–414)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for a specific workspace connection flow. It turns an OAuth state string into a short hashed connection id so the returned account can later be tied back to the workspace safely.

**Data flow**: It receives a workspace UUID and state string → hashes the state with SHA-256 → takes the first 32 hexadecimal characters → appends that to the workspace user prefix → returns the resulting external user id.

**Call relations**: OAuth setup code can use this when minting a connect token for a particular connection attempt. The ownership checker later recognizes the same format through workspace_user_prefix and its length/hex checks.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 417–425)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Validates and parses an HTTP response from Pipedream. It turns bad status codes or unexpected response shapes into clear PipedreamError exceptions.

**Data flow**: It receives an httpx response → if the status code is 400 or higher, raises PipedreamError with the response text → if the body is empty, returns an empty dictionary → otherwise parses JSON and confirms it is an object → returns that dictionary.

**Call relations**: access_token, _get, and _post all send responses through _body before higher-level code sees them. This makes the rest of the client work with clean dictionaries instead of raw HTTP responses.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 428–446)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds the default PipedreamClient from environment variables. It fails immediately if the deployment is missing the Pipedream client id, client secret, or project id needed to broker OAuth.

**Data flow**: It reads Pipedream configuration values from the process environment → checks that the required three values are present → reads the optional environment name or uses production → returns a configured PipedreamClient instance.

**Call relations**: Other parts of the extension call this when they need the real deployment client rather than a test client. It is the configuration entry point for this file, while the PipedreamClient methods do the later network work.

*Call graph*: 1 external calls (__init__).


### Configured connector backends
Additional backend integrations cover API-key connector manifests and workspace-configured MCP servers.

### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `extension manifest load`

Some external services do not use a brokered login flow. Instead, they expect a workspace-owned API key to be sent in a request header. This file describes those services in one place: what host they live at, what secret values they need, what request headers those secrets belong in, and what environment variable names the sandbox should see.

The important safety idea is that the sandbox does not receive the real key. It receives a placeholder value, called a sentinel, in an environment variable. When the sandbox makes an outgoing request to the approved host, the egress proxy swaps that sentinel for the real secret on the wire. Think of it like a coat-check ticket: the sandbox holds the ticket, while the real coat stays behind the counter until the exact right handoff.

The file also supports providers whose API host depends on the customer’s region or site. For example, a Datadog account may use a US, EU, or government API host. Those providers list the allowed hosts, and the member chooses one. This prevents a secret meant for one host from being sent somewhere else.

At the end, the file builds a manifest: a compact declaration of all credential slots and a help section explaining how agents should use these keyed providers.

#### Function details

##### `KeyedSecret.__post_init__`  (lines 56–61)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a declared secret uses only an authentication prefix the egress proxy knows how to replace safely. It catches bad provider declarations early, before they can become unsafe or unusable configuration.

**Data flow**: A KeyedSecret is created with a key name, request header, environment variable name, description, and optional scheme such as Bearer. After creation, this check reads the scheme. If there is no scheme, or it is one of the allowed schemes, nothing changes. If the scheme is unknown, it raises an error instead of allowing the declaration.

**Call relations**: This runs automatically when a KeyedSecret row is constructed inside the provider table. Later, KeyedProvider.slots relies on these secrets being valid when it turns them into credential slots and injection rules.


##### `KeyedProvider.__post_init__`  (lines 79–88)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider has a clear and safe API host rule. A provider must either have one fixed host or a closed list of selectable hosts, but not both and not neither.

**Data flow**: A KeyedProvider is created with its provider name, label, secrets, and either a fixed host or a list of possible sites. This check reads those fields. If the host setup is valid, the provider stays unchanged. If the setup is ambiguous or incomplete, it raises an error explaining what is missing.

**Call relations**: This runs automatically while the provider table is being built. It protects later steps, especially KeyedProvider.target_host and KeyedProvider.slots, from having to guess where a secret is allowed to be sent.


##### `KeyedProvider.target_host`  (lines 91–100)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This tells the rest of the file what host rule applies to a provider. It returns either one fixed hostname or a controlled host choice that the workspace member can select from.

**Data flow**: It reads the provider’s host and sites fields. If the provider has a fixed host, that hostname comes out directly. If the provider has selectable sites, it builds a HostChoice object containing the credential slot name for the choice, the human description, the allowed host list, the default host, and the environment variable used in the sandbox.

**Call relations**: KeyedProvider.slots uses this when building credential slots, so each secret is tied to the right destination. KeyedProvider.usage also uses it when writing the human-facing curl example, choosing either a literal host or an environment variable.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 102–120)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This converts one provider declaration into the credential slots the system can ask a workspace owner to fill. Each slot says what secret is needed and exactly where it may be injected into outgoing requests.

**Data flow**: It starts with a KeyedProvider and reads its target host and secrets. For each secret, it creates a CredentialSlot with a name, description, and InjectionTarget. The injection target records the approved host, request header, sentinel value, sandbox environment variable, and request dimension. If the provider also needs a host choice, it adds one extra credential slot for that choice. The result is a tuple of credential slots ready for the manifest.

**Call relations**: The top-level manifest function gathers the slots returned by every provider. This is the bridge between the simple provider table in this file and the wider credential system that requests, stores, and injects secrets.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 122–135)

```
def usage(self) -> str
```

**Purpose**: This writes a short, human-readable usage line for one provider. It explains which slots exist and shows the shape of a curl command that would call the provider’s API from the sandbox.

**Data flow**: It reads the provider’s secrets, schemes, environment variable names, and host rule. It formats the needed request headers, chooses either the fixed host or the host environment variable, and lists the credential slot names. The output is a single Markdown bullet used in the prompt help text.

**Call relations**: The module-level SECTION_BODY calls this for every provider while building the prompt section. That prompt section is then included by manifest, so agents and users get practical guidance alongside the credential declarations.


##### `manifest`  (lines 284–290)

```
def manifest() -> Manifest
```

**Purpose**: This is the file’s public entry point for the extension system. It packages all keyed-provider credential slots and the explanatory prompt text into one Manifest object.

**Data flow**: It reads the module constants, the provider table, and the already-built section body. It asks each provider for its credential slots, flattens them into one tuple, creates a prompt section with the keyed-connector instructions, and returns a Manifest containing the extension name, version, credentials, and prompt section.

**Call relations**: The extension loader calls this when it needs to discover what this extension contributes. The returned manifest is what lets the wider system know which credentials can be requested and what guidance should be shown for using these API-key-based providers.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

MCP, or Model Context Protocol, is a standard way for an outside service to publish tools that an AI agent can use. This file is the bridge between UFO and those outside MCP servers. Without it, the agent would not be able to browse or call workspace-specific MCP tools, such as a private search service or company documentation tool.

The file exposes two agent tools. The first, `list_mcp_tools`, asks a named MCP server what tools it has. To avoid flooding the agent with huge schemas, it first returns a compact catalog: names, short summaries, parameter names, and required fields. If the agent wants to use a tool, it can ask again for the full schema for only that tool. The second tool, `call_mcp_tool`, sends arguments to one chosen MCP tool and returns the result.

Workspace owners configure servers in a secret credential slot called `mcp_servers`. The file validates that server URLs are HTTP or HTTPS, preserves existing tokens when updating a server unless a new token is supplied, and supports removing servers. Calls are made with FastMCP's streamable HTTP client, which takes care of the MCP handshake and message framing. The file also sets size limits on requests and responses, because external servers are untrusted and could return too much data.

#### Function details

##### `McpServer._http_url`  (lines 79–82)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validator makes sure a configured MCP server address starts with HTTP or HTTPS. It prevents storing server URLs that this extension cannot safely or correctly contact.

**Data flow**: It receives a URL string from configuration validation. It checks the string against the allowed URL pattern. If the URL is acceptable, the same string is returned; if not, validation fails with a clear error.

**Call relations**: This runs automatically when an `McpServer` value is created or parsed, including during credential updates and server lookup. It is an early gatekeeper before any network client is built.


##### `McpServerUpdate._name`  (lines 97–101)

```
def _name(cls, value: str) -> str
```

**Purpose**: This validator cleans and checks the server name when someone adds or updates one configured MCP server. It makes sure the name is not empty after trimming spaces.

**Data flow**: It receives the submitted server name. It strips leading and trailing whitespace, rejects an empty result, and returns the cleaned name for storage.

**Call relations**: It runs as part of validating an update inside `merge_mcp_server`. That means bad names are rejected before the credential slot is changed.


##### `McpServerRemoval._name`  (lines 110–114)

```
def _name(cls, value: str) -> str
```

**Purpose**: This validator cleans and checks the server name when someone asks to remove a configured MCP server. It prevents a blank removal request from accidentally matching nothing or causing confusing behavior.

**Data flow**: It receives the submitted name, trims whitespace, rejects it if nothing remains, and returns the cleaned name.

**Call relations**: It runs during the removal path in `merge_mcp_server`. The removal logic only proceeds after this validator has confirmed there is a real server name to remove.


##### `merge_mcp_server`  (lines 117–155)

```
def merge_mcp_server(current: str | None, submitted: str) -> str
```

**Purpose**: This function updates the stored `mcp_servers` credential value. It supports replacing all servers, adding or updating one server, and removing one server, while validating names, URLs, and optional bearer tokens.

**Data flow**: It receives the current stored JSON value, if any, and a newly submitted JSON string. It parses the submission, decides whether it is a full replacement, a removal, or a single-server update, validates the shape, merges it with the existing server map when needed, and returns a cleaned JSON string to store. If the submission is invalid, it raises `CredentialValueInvalid` so the bad credential is not saved.

**Call relations**: This is attached to the `mcp_servers` credential slot by `manifest`. It is called when a workspace changes MCP server credentials, before `_server`, `_list_mcp_tools`, or `_call_mcp_tool` ever try to use those settings.

*Call graph*: 4 external calls (__init__, __init__, __init__, loads).


##### `mcp_client`  (lines 175–181)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This function builds the HTTP client used to talk to one MCP server. If the workspace configured an auth token, it sends that token as a bearer token in the request headers.

**Data flow**: It receives a validated `McpServer` object containing a URL and optional auth token. It creates HTTP headers if needed, builds a streamable HTTP transport for that URL, wraps it in a FastMCP client with a timeout, and returns the ready-to-use client.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` finds the requested server. FastMCP then takes over the lower-level MCP details, such as connection setup and protocol messages.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 184–196)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This function looks up one named MCP server from the workspace's stored credentials. It fails clearly if the tool was called without extension context, if credentials are missing, or if the requested name is not configured.

**Data flow**: It receives the current tool context and a server name. It reads the `mcp_servers` credential slot, parses it into validated server objects, searches for the requested name, and returns the matching `McpServer`. If the name is absent, it reports which server names are available.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` both start by calling this. It is the shared checkpoint that turns a user-facing server name into the actual URL and token needed by `mcp_client`.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 199–220)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing `list_mcp_tools` tool. It either returns a compact catalog of all tools on a configured MCP server or returns full input schemas for specific tools the agent plans to call.

**Data flow**: It receives the tool context and validated input containing a server name plus optional tool names. It looks up the server, connects to it, asks for its tool list, and then formats the answer. With no requested tool names, it returns short catalog entries; with requested names, it verifies each exists and returns detailed schema entries, subject to size checks.

**Call relations**: This function is registered in `manifest` as the handler for `list_mcp_tools`. It relies on `_server` and `mcp_client` to reach the right server, then uses `_catalog_entry`, `_schema_entry`, `_bounded_schemas`, and `_json_result` to shape the response for the agent.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 223–225)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This helper reads whether an MCP tool says it is idempotent, meaning repeated calls should have the same effect as one call. That hint helps describe tools more safely to the agent.

**Data flow**: It receives one MCP tool object. It checks the tool's optional annotations for an `idempotentHint` value and returns `true` or `false`; missing annotations count as `false`.

**Call relations**: `_catalog_entry` and `_schema_entry` call this while preparing tool information. It adds a small safety hint to both compact listings and full schema responses.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 228–244)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This function turns one full MCP tool description into a short catalog item. The catalog is meant for browsing, like a menu that shows dish names and a short note rather than the full recipe.

**Data flow**: It receives an MCP tool object. It reads the tool name, description, input schema properties, required parameters, and idempotency hint. It returns a small JSON-friendly object with the tool name, short summary, parameter names, required parameter names, and idempotent flag.

**Call relations**: `_list_mcp_tools` calls this for every tool when the agent asks to browse a server without requesting full schemas. It uses `_summary` to keep descriptions short and `_idempotent` to include the safety hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 247–253)

```
def _summary(description: str) -> str
```

**Purpose**: This helper extracts a short human-readable summary from a longer tool description. It keeps the catalog small and avoids dragging in long argument documentation.

**Data flow**: It receives a description string. It trims it, takes the first line, keeps only the text before the first sentence break, limits it to the configured maximum length, and returns that short summary.

**Call relations**: `_catalog_entry` calls this while building browseable tool listings. It is part of the file's two-step design: show a compact overview first, then provide full schemas only when requested.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 256–262)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This function builds the detailed listing for one MCP tool. It includes the full input schema so the agent can spell parameters correctly before calling the tool.

**Data flow**: It receives an MCP tool object. It copies out the tool name, full description, input schema, and idempotent flag, then returns them as a JSON-friendly object.

**Call relations**: `_list_mcp_tools` calls this only after the agent names specific tools. It uses `_idempotent` for the repeated-call safety hint, then hands the result to `_bounded_schemas` for size-aware packaging.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 265–277)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing `call_mcp_tool` tool. It sends a JSON argument object to a named tool on a configured MCP server and returns the server's result.

**Data flow**: It receives the tool context and validated input containing the server name, tool name, and arguments. It looks up the server, checks that the serialized arguments are not too large, connects to the server, and calls the requested tool. If the server reports an error, it returns an error tool result with bounded text; if the server returns structured JSON content, it returns that; otherwise it joins text blocks and returns them as JSON.

**Call relations**: This function is registered in `manifest` as the handler for `call_mcp_tool`. It depends on `_server` and `mcp_client` to make the MCP round trip, and on `_joined_text`, `_bounded`, and `_json_result` to turn the outside server's response into a safe result for the agent.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 280–281)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This helper gathers plain text blocks from an MCP response into one string. It ignores non-text blocks because this extension returns either structured JSON or joined text.

**Data flow**: It receives a list of content blocks from an MCP result. It keeps only blocks that are MCP text content, extracts their text, joins them with newline characters, and returns the combined string.

**Call relations**: `_call_mcp_tool` uses this when an MCP call fails or when the result has no structured JSON content. The joined text is then bounded or wrapped into a JSON result.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 284–287)

```
def _bounded(text: str) -> str
```

**Purpose**: This helper enforces the maximum allowed MCP response size. It fails loudly instead of silently cutting off data, because a truncated tool result could mislead the agent.

**Data flow**: It receives a text string. It measures the string as bytes, rejects it with `McpError` if it is larger than the configured limit, and otherwise returns the original string unchanged.

**Call relations**: `_call_mcp_tool` uses this for error text from a failed MCP call. `_json_result` uses it for all JSON responses before placing them into a tool result.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 290–307)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This function checks whether a request for several full tool schemas is too large to return usefully. It asks the agent to request fewer schemas when a smaller, better-targeted request is possible.

**Data flow**: It receives a JSON payload containing schema entries and the number of tools requested. If more than one schema was requested and the JSON is too large for a normal listing, it raises an error explaining how to narrow the request. Otherwise, it passes the payload to `_json_result` and returns the resulting tool response.

**Call relations**: `_list_mcp_tools` calls this after building full schema entries. It sits between schema collection and final response formatting, protecting the agent from partial or unhelpful multi-schema output.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 310–311)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This helper wraps a JSON-friendly payload into the standard UFO tool-result shape. It also enforces the response-size limit before returning the result.

**Data flow**: It receives a dictionary payload. It serializes the payload to JSON text, checks the text with `_bounded`, puts the text into a `TextContent` object, and returns a `ToolResult` containing that content.

**Call relations**: `_list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` use this whenever they need to send a successful JSON result back to the agent. It is the common final packaging step for most responses in this file.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 314–345)

```
def manifest() -> Manifest
```

**Purpose**: This function declares the extension to UFO. It names the extension, registers the two MCP tools, and defines the credential slot used to store workspace MCP server settings.

**Data flow**: It takes no input. It creates a `Manifest` containing two `ToolDef` entries, one for listing MCP tools and one for calling them, plus a `CredentialSlot` named `mcp_servers` that uses `merge_mcp_server` when credentials change. It returns that manifest to the host system.

**Call relations**: The UFO extension loader calls this to discover what the file offers. The manifest connects outside callers to `_list_mcp_tools`, `_call_mcp_tool`, and `merge_mcp_server`, making the rest of the file reachable at runtime.

*Call graph*: 3 external calls (__init__, __init__, __init__).

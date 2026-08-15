# Core connector brokering and account grants  `stage-16.1`

This stage is shared behind-the-scenes support for letting agents use outside services without handing them raw secrets. It acts like a guarded front desk between the workspace, the agent, and services such as Gmail, GitHub, Slack, or brokered tools.

The core connector code defines that boundary. It makes sure connector calls can get the credentials they need, while keeping passwords, API keys, and tokens out of agent prompts, sandboxes, and logs. The credentials code stores those secrets safely and creates short-lived private authorization requests when a user must enter or approve one. The grants code records which workspace member connected an outside account and which agent is allowed to use it, including sharing, revoking, and disconnecting access.

The connector extension turns those accounts and permissions into normal workspace objects, so users can inspect and manage them consistently. Its tools file gives agents the live doorway to discover connectors, see available actions, call them, and move files safely. The MCP extension adds the same kind of tool access for Model Context Protocol servers. The evaluation extension provides fake mail, calendar, and code-search connectors so tests can run predictably through the same path.

## Files in this stage

### Connector security core
Defines the trusted boundary for connector execution, sealed credential handling, and OAuth account grants.

### `core/src/ufo/connectors.py`

`domain_logic` · `cross-cutting connector discovery, credential resolution, sync, and brokered request forwarding`

Connectors need to talk to outside services, but those services require private credentials. This file is the rulebook and routing layer for doing that safely. It separates two cases: a direct credential, such as a member-provided API key stored by the workspace, and a brokered connection, where another service holds the token and UFO only sends requests through that broker.

The central idea is the Credential object. It can contain exactly one usable way to authenticate: a special HTTP transport that sends requests through a broker, a bearer token, or custom headers. Its printed form always hides the secret, like an envelope that only says “contains credential” without showing what is inside.

ConnectorRegistry is the map from provider names to the broker that owns them. It can also ask an open resolver about providers that were not registered one by one. SourceCredentialResolver adds an extra safety check for feed-sync jobs: if a source was tied to a specific member-owned connection, every credential lookup and every proxied request must still match that active database connection. This prevents an old or changed connection from silently continuing to sync data.

The file also defines small shared shapes for broker tools, file uploads, file outputs, catalog entries, and forwarded HTTP responses. These are the common vocabulary that lets brokers, dynamic tools, feed sync, and the egress proxy cooperate without passing raw secrets or file bytes through the wrong place.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a credential for debugging. It deliberately hides bearer tokens, headers, and broker transports so an accidental log line does not leak a secret.

**Data flow**: It reads which credential path is present on the object. Instead of returning the actual token, headers, or transport details, it returns a short redacted label such as a bearer credential, header credential, transport credential, or empty credential.

**Call relations**: This method is used automatically by Python when a Credential is printed or included in an error display. It supports the whole connector flow by making Credential safer to carry through normal debugging and exception paths.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Describes the promise that any authentication backend must keep: given a workspace, provider, and account, return a Credential that connector code can use. Different implementations may fetch a direct key or create a broker-backed transport.

**Data flow**: The caller supplies the workspace ID, provider name, and account handle. The implementation looks up or prepares the right authentication method and returns a Credential object; the protocol itself only defines the shape of that exchange.

**Call relations**: Feed-sync code and SourceCredentialResolver rely on this common method without caring whether the backend is direct or brokered. Concrete auth backends implement it elsewhere.


##### `stale_grant_guidance`  (lines 93–100)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a human-readable explanation for a connection grant that the current broker no longer recognizes. It tells the agent or user that retrying is not enough and the member needs to reconnect.

**Data flow**: It takes a provider name, inserts it into a fixed explanatory sentence, and returns that sentence. It changes no state.

**Call relations**: Broker implementations can use this message when they detect an old or mismatched account grant. It keeps user-facing guidance consistent across connector backends.


##### `ConnectorBroker.tools`  (lines 170–172)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists the tools it offers for a provider, optionally filtered by a search query. This is how dynamic connector tools discover what actions are available.

**Data flow**: The caller provides a workspace ID, provider name, and query text. A broker implementation returns matching BrokerTool descriptions; this protocol method only defines the contract.

**Call relations**: Dynamic tool discovery calls this through the broker stored in ConnectorRegistry. Individual broker extensions provide the actual catalog lookup.


##### `ConnectorBroker.schema`  (lines 174–174)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the detailed input shape for one tool. The input shape tells the agent what arguments the tool accepts.

**Data flow**: The caller gives a workspace, provider, and tool slug. The broker returns a BrokerTool with its schema filled in, or signals that the tool slug is unknown.

**Call relations**: Describe-style connector flows call this after finding or naming a tool. Broker implementations may raise UnknownBrokerTool so callers can report an unresolved tool rather than crashing unexpectedly.


##### `ConnectorBroker.execute`  (lines 176–184)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool using an already granted account. The broker, not UFO or the sandbox, injects the real provider secret.

**Data flow**: The caller sends the workspace, provider, tool slug, argument values, account ID, and an optional idempotency key, which is a repeat-safe request key. The broker runs the tool and returns a dictionary response from that execution.

**Call relations**: Dynamic connector tool execution routes through ConnectorRegistry to a broker and then calls this method. The broker implementation talks to its own execute API or provider integration.


##### `ConnectorBroker.file_outputs`  (lines 186–186)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts downloadable files from a tool execution response. It turns broker-specific response data into a shared list of file references.

**Data flow**: It receives the response dictionary from a brokered tool call. The implementation reads any file output information and returns BrokerFile objects containing filenames and temporary download URLs.

**Call relations**: After ConnectorBroker.execute returns, connector tooling can ask the same broker to identify file outputs. The sandbox then fetches those files directly from the broker’s file store rather than through the serve process.


##### `ConnectorBroker.stage_upload`  (lines 188–196)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file so a connector tool can consume it. It creates a temporary upload target or says the broker already has the file.

**Data flow**: The caller provides workspace, provider, tool slug, filename, MIME type, and file checksum. The broker returns upload instructions: where to PUT the bytes, what content type to use, and what argument value should be passed to the tool.

**Call relations**: Before executing a tool that needs a file, dynamic connector tooling asks the broker to stage the upload. The sandbox performs the actual file transfer directly, keeping file bytes out of the serve process.


##### `ConnectorBroker.search`  (lines 198–198)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines a richer semantic search over a broker’s tools. It can return matching tools plus a plan, guidance, and warnings.

**Data flow**: The caller gives a workspace, provider, and search query. The broker returns a BrokerSearch result containing tool schemas and optional explanatory notes.

**Call relations**: Connector discovery or planning features can call this when simple listing is not enough. Broker implementations decide whether they support deeper search or return a mostly empty guidance section.


##### `ConnectorBroker.credential`  (lines 200–200)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker supplies a Credential for feed-sync access to a provider account. For brokered accounts, this usually means returning a transport that proxies requests through the broker.

**Data flow**: The caller provides workspace, provider, and account handle. The broker verifies or resolves the account and returns a Credential that the source can use without seeing the raw provider token.

**Call relations**: _credential calls this when the source account is not the direct account. Broker implementations are responsible for keeping the real secret server-side.


##### `RequestForwarder.forward`  (lines 219–221)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how the egress proxy forwards one captured provider HTTP request through a broker. This lets command-line tools in the sandbox authenticate with a harmless marker while the broker supplies the real credential.

**Data flow**: The caller gives the granted account ID, HTTP method, URL, headers, and body bytes. The implementation sends the request through the broker and returns the provider’s status, headers, and body in a ForwardedResponse.

**Call relations**: CliCredential objects carry a RequestForwarder so the egress proxy can hand matched requests to the right broker. Concrete brokers implement the forwarding behavior.


##### `ConnectorResolver.transfer_hosts`  (lines 268–268)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Names the file-store hosts that grants from an open connector namespace are allowed to contact. This supports safe file upload and download through the egress proxy.

**Data flow**: A resolver implementation returns a tuple of hostnames. No input is needed beyond the resolver’s own configuration.

**Call relations**: Connector infrastructure reads this property when deciding which broker file-transfer destinations a grant should admit. Implementations live in broker extensions.


##### `ConnectorResolver.claims`  (lines 270–270)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Answers whether an open connector resolver serves a provider slug. This avoids assuming that every unknown provider belongs to the resolver.

**Data flow**: The caller supplies a provider name. The resolver checks its live catalog or rules and returns true or false.

**Call relations**: Code choosing between a broker namespace and other credential options can ask this before routing. The protocol lets broker extensions implement that check with their own external catalog.


##### `ConnectorResolver.entry`  (lines 272–272)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a ConnectorEntry for a provider served by an open resolver. It turns a provider slug into the registry-style route used elsewhere.

**Data flow**: The caller gives a provider name. The resolver returns a ConnectorEntry containing that provider, a label, and the shared broker that should serve it.

**Call relations**: ConnectorRegistry.entry and _credential use this when a provider is not explicitly registered but a resolver is installed. The returned entry then leads callers to the broker.


##### `ConnectorResolver.catalog`  (lines 274–274)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches the live catalog of connectable services behind an open resolver. This lets discovery show services that were not hard-coded into the registry.

**Data flow**: The caller supplies query text and a maximum result count. The resolver searches its broker-backed service catalog and returns CatalogEntry results.

**Call relations**: ConnectorRegistry.search_catalog delegates to this method when an open resolver exists. Broker extensions provide the actual live search.


##### `ConnectorRegistry.entry`  (lines 291–297)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the connector route for a provider. It first checks explicitly registered providers, then falls back to an open resolver if one exists.

**Data flow**: It receives a provider name and looks in the registry’s entries map. If found, it returns that ConnectorEntry; otherwise it asks the resolver to build one; if neither path works, it raises a clear KeyError.

**Call relations**: Dynamic connector tools use this kind of lookup before describing or executing provider tools. It is the local traffic director that points a provider name to the broker responsible for it.


##### `ConnectorRegistry.search_catalog`  (lines 299–304)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns extra connectable services from the open resolver, if the deployment has one. If there is no resolver, it returns an empty result rather than failing.

**Data flow**: It receives a search query and result limit. It either returns an empty tuple or passes the query and limit to the resolver’s catalog search and returns those catalog entries.

**Call relations**: Discovery flows call this to add open-namespace providers to the list of known connectors. It hands the actual live lookup to ConnectorResolver.catalog.


##### `_credential`  (lines 307–324)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right authentication path for a feed-sync source. Brokered accounts go to the provider’s broker; the special direct account goes to the fallback direct auth backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is brokered, it finds the registered broker or resolver-backed broker and asks it for a Credential; if the account is direct, it asks the fallback AuthProxy. If no suitable route exists, it raises an error.

**Call relations**: _BoundSourceCredentials.credential calls this after applying source-specific safety checks. This helper is the shared credential router underneath source credential binding.

*Call graph*: called by 1 (credential).


##### `_require_source_connection`  (lines 327–351)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Verifies that a feed-sync source is still allowed to use the exact member-owned connection it was bound to. This prevents stale, deleted, or swapped connections from being reused silently.

**Data flow**: It receives workspace, connection, owner member, provider, and account IDs. It opens the workspace database context, queries the connection table for an exact active match, and returns nothing if found; if no match exists, it raises a ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before issuing brokered credentials, and _ConnectionTransport.handle_async_request calls it before each proxied HTTP request. It uses the workspace transaction and SQL query helpers to read the authoritative connection record.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 363–371)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Wraps a broker-provided HTTP transport with a fresh connection check before every request. This makes sure a long-running sync cannot keep using a connection after it is removed or changed.

**Data flow**: It receives an HTTP request from httpx, first verifies the stored workspace connection details with _require_source_connection, then forwards the unchanged request to the inner transport and returns the HTTP response.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper around brokered credential transports. During sync HTTP calls, httpx invokes this method, and this method delegates the actual network behavior to the broker’s inner transport only after authorization is confirmed.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 373–374)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is finished. This releases any network resources held by the inner transport.

**Data flow**: It takes no new data beyond the wrapper object. It calls the inner transport’s close method and returns nothing.

**Call relations**: HTTP client cleanup calls this as part of normal transport teardown. It simply passes cleanup through to the transport supplied by the broker.


##### `_BoundSourceCredentials.credential`  (lines 383–413)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Provides credentials for one source after enforcing whether that source is direct or bound to a specific connection. It is the guardrail between feed-sync code and the broader connector registry.

**Data flow**: It receives workspace, provider, and account. For the direct account, it rejects use if the source was connection-bound, then asks _credential for fallback direct credentials. For brokered accounts, it requires stored connection details, checks the connection in the database, asks _credential for a broker credential, confirms that credential uses a proxy transport, and returns a new Credential wrapping that transport in _ConnectionTransport.

**Call relations**: SourceCredentialResolver.bind creates this object for a particular source. Feed-sync code then calls its credential method; inside, it relies on _require_source_connection for authorization and _credential for registry routing.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 420–425)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy for a specific feed-sync source, optionally tied to a particular member-owned connection. This turns the general connector registry into a source-specific credential resolver.

**Data flow**: It receives a connection ID and owner member ID, which may be absent for direct credentials. It packages those values with the registry into a _BoundSourceCredentials object and returns it as the AuthProxy the source should use.

**Call relations**: The sync runner calls this when preparing a source. The returned _BoundSourceCredentials object performs the later credential lookup and safety checks.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting`

This file is the credential safety box for the system. A workspace may need outside secrets, like a GitHub token, but those secrets must not appear in logs, chat history, or code running in the sandbox. The file solves that by encrypting stored values with Fernet, which is a standard symmetric encryption tool where the same configured key locks and unlocks the data.

There are two main paths. First, it stores and retrieves real credential values through `CredentialStore`. Values are encrypted before they go into the database and decrypted only when needed. If a value is missing, callers get a clear “slot is unset” signal instead of an empty or fake secret.

Second, it seals temporary credential actions. A seal is like a tamper-proof ticket: it says which workspace, member, and slot the action belongs to, and it expires unless it is an installation binding meant to last. This lets the system ask a user to fill a private prompt, or complete an external provider authorization, and then verify that the returned value really belongs to the right request.

The file also deals with credential slot names, provider-chosen hosts, and credentials minted by providers instead of typed by members. The important idea is consistency: sandbox setup, proxy rules, and provider flows all ask the same helper functions, so the system does not export one answer while the proxy enforces another.

#### Function details

##### `credential_object_name`  (lines 28–31)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a declared credential slot name into a simple object name that can be used consistently elsewhere. It lowercases the name, replaces punctuation or spaces with dashes, and trims extra dashes.

**Data flow**: It receives a slot name as text. It normalizes that text into a safe, slug-like name. It returns the cleaned name and changes nothing else.

**Call relations**: When `named_slots` needs a public-facing name for each declared slot, it calls this helper first. This gives all later readers and actions the same basic name for the same slot.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `named_slots`  (lines 34–49)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Builds the map from public credential object names to the actual declared slots. It also handles the rare case where two slots would clean up to the same name by adding a short stable fingerprint.

**Data flow**: It receives all declared slots. It groups them by their cleaned object name, keeps unique names as-is, and adds a short hash-based suffix when names collide. It returns a dictionary from final object name to slot.

**Call relations**: It relies on `credential_object_name` to make the first version of each name. If there is a collision, it uses a SHA-256 digest so different extensions can still be addressed unambiguously.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 93–94)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Creates an encrypted, tamper-resistant seal for a credential-related action. Someone can later open this seal to prove what workspace, member, slot, and purpose the action was created for.

**Data flow**: It receives a Fernet encryption object and a `CredentialRequestState`. It turns the state into JSON, encrypts it, and returns the encrypted text. It does not store anything by itself.

**Call relations**: `CredentialRequests.seal`, `CredentialRequests.authorize`, and `seal_installation` use this as the common way to create sealed tickets. Those callers decide what the ticket means; this function only locks the contents.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 97–123)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Opens and verifies a sealed credential request. It rejects seals that are expired, forged, malformed, or meant for a different purpose.

**Data flow**: It receives a Fernet object, encrypted seal text, the expected purpose, and optionally a time limit. It decrypts the seal, parses the JSON into a state object, checks the purpose, and returns the state. If anything is wrong, it raises `CredentialRequestInvalid`.

**Call relations**: `CredentialRequests.open_authorization`, `authorized_slot_workspace`, and `open_installation` call this before doing their more specific checks. It is the shared gatekeeper that keeps request seals and installation seals from being confused with each other.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 142–155)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a short-lived seal for member-entered credential values. It refuses slots that are not declared by an installed extension, and refuses slots that members are not allowed to type manually.

**Data flow**: It receives a workspace ID, member ID, and the slots the member is being asked to fill. It checks those slots against the declared and fillable sets, builds a request state, encrypts it, and returns the seal text. If the request names invalid slots, it raises an error before any prompt can be fulfilled.

**Call relations**: User-facing credential request flows call this before showing a private prompt. It hands the final sealing work to `seal_credential_request`, which produces the encrypted ticket that later fulfillment can verify.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 157–170)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a seal for an external provider authorization flow, such as an OAuth-style redirect. The seal records which workspace, member, slot, and provider state belong together.

**Data flow**: It receives a workspace ID, member ID, one slot name, and provider payload text. It checks that the slot is declared and the payload is not empty, then creates and encrypts a request state. It returns the seal used to connect the outgoing authorization request to the later callback.

**Call relations**: Provider authorization setup calls this before sending the user away to a provider. It uses `seal_credential_request` so the callback can later prove it is returning for the same slot and member.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 172–186)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a returned provider authorization seal and extracts the provider state inside it. It makes sure the seal belongs to the exact workspace, member, and slot expected.

**Data flow**: It receives sealed text plus the expected workspace ID, member ID, and slot. It opens the seal, compares every important field, checks that provider state is present, and returns that state. If any part does not match, it raises `CredentialRequestInvalid` or a slot declaration error.

**Call relations**: Provider callback or fulfillment code calls this after receiving a seal back. It first delegates decryption and purpose checking to `open_credential_request`, then performs the provider-specific identity checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 189–203)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Creates a long-lived sealed binding between a workspace and a provider installation ID. This prevents someone from typing or guessing a bare installation ID and using another organization’s installation.

**Data flow**: It receives a Fernet object, workspace ID, slot name, and installation ID. It wraps those into a credential request state marked with the installation-binding purpose, encrypts it, and returns the sealed text.

**Call relations**: Provider installation flows call this when they need to store an installation identity as a credential value. It uses `seal_credential_request`, but with a different purpose so it cannot be mistaken for a normal member credential request.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 206–217)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a stored installation binding and returns the provider installation ID only if it truly belongs to the expected workspace and slot.

**Data flow**: It receives a Fernet object, expected workspace ID, expected slot, and sealed binding text. It opens the seal without an expiry time, checks the workspace and slot, checks that an installation ID is present, and returns that ID. Invalid or mismatched bindings raise `CredentialRequestInvalid`.

**Call relations**: Provider code calls this when it needs to use a stored installation. It relies on `open_credential_request` to reject forged or wrong-purpose seals before checking the workspace and slot details.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 223–229)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Installs the process-wide credential request authority used by routes that cannot get it from a normal request context. This is mainly for provider callbacks that arrive through a browser redirect.

**Data flow**: It receives a `CredentialRequests` object, or `None` if credential requests are unavailable. It stores that value in a module-level variable. It returns nothing, but it changes what later global lookup functions will see.

**Call relations**: Startup code calls this once when the server is configured. Later, callback-oriented helpers such as `installed_credential_requests` and `authorized_slot_workspace` depend on the installed value.


##### `installed_credential_requests`  (lines 232–235)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or fails clearly if credential support is not configured. It gives code a single place to ask for the installed Fernet-backed request helper.

**Data flow**: It reads the module-level installed request object. If one exists, it returns it. If not, it raises a runtime error explaining that no credential key is configured.

**Call relations**: Routes or services that need the globally installed credential request helper call this after startup. It is paired with `install_credential_requests`, which sets the value it returns.


##### `authorized_slot_workspace`  (lines 238–254)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Finds which workspace a provider authorization seal belongs to, but only if the seal is valid for the exact slot and provider payload expected. It returns `None` instead of raising for invalid callback input.

**Data flow**: It receives sealed text, a slot name, and expected payload text. It opens the seal with the installed Fernet key, checks the slot and payload, and returns the workspace ID if everything matches. If credential requests are not installed or the seal is invalid, it returns `None`.

**Call relations**: Provider callback routes use this when they receive a browser redirect without a normal user session. It calls `open_credential_request` to verify the seal, then uses the seal contents to identify the workspace safely.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 261–283)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a credential value for a workspace and slot, encrypting it before it reaches the database. It updates an existing slot or creates it if it does not exist.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects empty values, encrypts the secret, opens a workspace database transaction, updates the matching row if present, or inserts a new row otherwise. It returns nothing, but the database ends up holding encrypted ciphertext.

**Call relations**: Credential fulfillment and provider binding code use this when a value is ready to be saved. It uses the database transaction helper and SQLAlchemy insert/update operations to write the encrypted value safely.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 285–297)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Retrieves and decrypts a stored credential value for a workspace and slot. It gives callers the real secret only after loading the encrypted value from the database.

**Data flow**: It receives a workspace ID and slot name. It queries the credential table for the encrypted value, raises `CredentialSlotUnset` if there is no row, decrypts the ciphertext if present, and returns the plaintext string.

**Call relations**: `slot_secret`, `slot_is_set`, and `credential_host` call this for ordinary stored credentials or choices. GitHub app token code also calls it when checking or minting provider-backed credentials.

*Call graph*: called by 5 (credential_host, slot_is_set, slot_secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 299–330)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing credential only if it still contains the expected old value. This protects refresh flows from overwriting a newer credential written by another task at the same time.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects an empty replacement, reads the current encrypted value, decrypts it for comparison, and only writes the new encrypted value if the old value matches and the database row has not changed. It returns `true` if the replacement happened and `false` otherwise.

**Call relations**: OAuth-style refresh code uses this after getting a new token from an outside provider. It performs its own database read and update inside a transaction so concurrent refresh attempts cannot silently clobber each other.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 354–359)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host-choice declaration is internally valid. The default host must be one of the allowed hosts.

**Data flow**: It reads the `default` and `hosts` fields after a `HostChoice` is created. If the default is not in the allowed host list, it raises a `ValueError`. Otherwise it leaves the object unchanged.

**Call relations**: This runs automatically when a `HostChoice` object is constructed. It catches bad extension declarations early, before `credential_host` or proxy setup rely on them.


##### `HostChoice.resolve`  (lines 361–366)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string. It accepts different capitalization from the stored value, but never returns free-form user text.

**Data flow**: It receives the selected value as text. It trims spaces, compares it case-insensitively against the allowed hosts, and returns the matching declared host. If there is no match, it returns `None`.

**Call relations**: `credential_host` uses this when a workspace has stored a choice for a variable provider host. This keeps the proxy and sandbox using only hosts that the extension explicitly declared.


##### `CredentialSource.secret`  (lines 374–374)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the interface for a provider-backed slot to produce a secret for a workspace. A provider-backed slot can mint a fresh token instead of using a member-stored value.

**Data flow**: An implementation receives a workspace ID and the credential store. It may read stored binding information, contact its provider, and return a secret string, or return `None` if it has nothing to mint. The protocol itself only defines that expected behavior.

**Call relations**: `slot_secret` calls this when a slot has a credential source. Concrete implementations, such as provider token sources, supply the actual minting behavior.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 376–384)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the interface for checking whether a provider-backed slot has enough binding information to mint a secret, without actually minting one. This avoids making provider network calls just to decide whether a sandbox should be configured.

**Data flow**: An implementation receives a workspace ID and credential store. It checks local binding state and returns `true` if the slot can produce a secret, or `false` if not. It may raise an error if the workspace has a binding that this deploy cannot use.

**Call relations**: `slot_is_set` calls this for source-backed slots. The actual answer comes from provider-specific implementations, while this protocol states what callers can rely on.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 387–402)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the main question: what secret should this slot use for this workspace? It prefers a provider-minted secret when available, then falls back to the stored member value.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store. If a source exists, it asks the source for a minted secret and returns it if one is available. Otherwise it tries to read the stored value; if the slot is unset, it returns `None`.

**Call relations**: Proxy rules, sandbox environment setup, and other credential consumers use this so they all resolve secrets the same way. It calls `CredentialSource.secret` for provider-backed slots and `CredentialStore.get` for stored values.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 405–423)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a slot would produce a secret without actually producing it. This is useful during sandbox setup, where the system only needs to know whether to configure a client.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and store. If there is a source and it reports that the workspace is bound, it returns `true`. Otherwise it looks for a stored value and returns `true` if found or `false` if the slot is unset.

**Call relations**: Sandbox-opening code can call this instead of `slot_secret` to avoid expensive provider minting. It uses `CredentialSource.bound` for provider-backed slots and `CredentialStore.get` for ordinary stored slots.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 426–443)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Resolves the host that a credential is allowed to be sent to for a workspace. It returns either a fixed declared host or a declared host selected by the workspace through a `HostChoice`.

**Data flow**: It receives the credential store, workspace ID, and either a plain host string or a `HostChoice`. Plain strings are returned unchanged. For a `HostChoice`, it reads the workspace’s stored selection, falls back to the default if none is stored, and returns the declared matching host or `None` if the stored choice is invalid.

**Call relations**: Both proxy admission rules and sandbox exports use this so they agree on the exact provider host. It calls `CredentialStore.get` when a host choice is stored in a credential slot, and then relies on `HostChoice.resolve` behavior to avoid trusting free-form host text.

*Call graph*: calls 1 internal fn (get).


### `core/src/ufo/grants.py`

`domain_logic` · `request handling and cross-cutting credential/grant operations`

This file is the project’s “permission slip” system for external accounts. A member owns a connection to a provider account, and an agent receives a grant that says it may use that connection. The actual secret token stays on the server-side broker, so the agent does not receive the password-like credential directly.

The main flow is an OAuth browser handoff. OAuth is a common sign-in-and-consent process where a user is sent to another service, approves access, and comes back with a short code. `ConnectFlow.authorize` creates the outgoing consent link. It seals important details, such as workspace, agent, member, provider, and conversation, into a protected `state` value. `ConnectFlow.complete` later opens that sealed state, exchanges the returned code for a broker account, and asks `GrantStore` to save the connection and grant.

`GrantStore` is the database-facing part. It makes sure one connected account has one owner in a workspace, allows existing shared connections to be attached, and checks whether a member or admin may change something before revoking, sharing, or deleting it. There are also summary helpers for audit and portal views, plus naming helpers so the same provider account gets a stable readable object name everywhere.

#### Function details

##### `grant_sentinel`  (lines 37–41)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a predictable placeholder value for a connected account. This placeholder can travel through command-line-style credentials without exposing the real secret.

**Data flow**: It takes an account id, adds a fixed prefix to it, and returns the combined string. Nothing is stored or changed.

**Call relations**: This is a small shared convention between the sandbox side that exports credentials and the proxy side that recognizes them. Both can compute the same marker from the account id without first registering it somewhere.


##### `OAuthProvider.provider`  (lines 88–88)

```
def provider(self) -> str
```

**Purpose**: Defines the provider name that a concrete OAuth connector must expose. The name is the stable label used when recording and looking up a connection.

**Data flow**: A concrete provider object supplies this value when asked. The protocol itself does not compute anything.

**Call relations**: Connect flow code reads this from the selected provider descriptor after OAuth succeeds, so the grant is recorded under the provider’s canonical name.


##### `OAuthProvider.host`  (lines 91–91)

```
def host(self) -> str
```

**Purpose**: Defines the provider host that a concrete OAuth connector must expose. The host tells the egress proxy which outside destination this grant allows.

**Data flow**: A concrete provider object supplies the host string when asked. The protocol only states that the value must exist.

**Call relations**: After a connection is completed, `ConnectFlow.complete` passes this host to `GrantStore.record` so later network access can be matched to the right grant.


##### `OAuthProvider.authorize_url`  (lines 93–93)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a connector builds the browser link where the member approves access. Each provider has its own URL shape, so implementations provide the details.

**Data flow**: It receives a sealed state value and a callback address, then returns the full URL to send the member to. The protocol does not store anything itself.

**Call relations**: `ConnectFlow.authorize` calls this after preparing the protected state. The returned URL is what the user-facing surface can open or memoize.


##### `OAuthProvider.exchange`  (lines 95–97)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a connector turns the provider’s returned code into a connected broker account. This is the step that proves the browser approval really completed.

**Data flow**: It receives the short code, callback address, workspace id, and state value. A concrete implementation contacts or checks the provider and returns an `OAuthAccount` containing the stable account id and optional display label.

**Call relations**: `ConnectFlow.complete` calls this during the callback leg, then records the resulting account through `GrantStore.record`.


##### `OAuthProviderResolver.claims`  (lines 108–108)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how an open provider namespace decides whether it recognizes a provider name. This is useful when a broker can serve many provider slugs without listing each one ahead of time.

**Data flow**: It receives a provider string and returns true or false, possibly after checking an external catalog. The protocol itself does not implement the check.

**Call relations**: `ConnectFlow.validate_provider` uses this to reject typos or unavailable providers before creating a dead authorization request.


##### `OAuthProviderResolver.descriptor`  (lines 110–110)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Defines how an open provider namespace builds an OAuth provider descriptor for a validated provider name. The descriptor is the object the rest of the connect flow can use uniformly.

**Data flow**: It receives a provider string and returns an `OAuthProvider`-compatible object. No database state is changed here by the protocol itself.

**Call relations**: `ConnectFlow._provider` uses this when the provider is not in the fixed provider map but a resolver is installed.


##### `GrantStore.workspace_id`  (lines 184–185)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently in effect. This keeps every grant operation scoped to the right workspace.

**Data flow**: It reads the current workspace context and returns its workspace id. It does not accept input or change data.

**Call relations**: Most `GrantStore` methods rely on this property before querying or writing database rows, so they do not accidentally cross workspace boundaries.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 188–189)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent id currently in effect. This lets the store know which agent is receiving, listing, or changing grants.

**Data flow**: It reads the current agent context and returns its agent id. It does not accept input or change data.

**Call relations**: Grant operations use this when creating or finding connector-grant edges for the bound agent.

*Call graph*: 1 external calls (agent_current).


##### `GrantStore.record`  (lines 191–284)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None) -> None
```

**Purpose**: Creates or reuses a member-owned connection and grants the current agent access to it. It prevents the same provider account from silently moving from one member to another.

**Data flow**: It receives provider, account id, host, grantor member, conversation, sharing choice, and optional account label. It rejects account ids with control characters, opens a workspace database transaction, inserts the connection if missing, confirms the owner, updates connection details, and inserts or refreshes the agent’s grant edge. It returns nothing, but the database now contains the connection and grant.

**Call relations**: `ConnectFlow.complete` calls this after a provider code has been exchanged for an account. It is the durable final step of the OAuth handoff.

*Call graph*: 7 external calls (__init__, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 286–323)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the current agent’s usable grants. This is how runtime code learns which connected accounts the agent may use.

**Data flow**: It reads the current workspace and agent from context, joins grant rows to their connection rows in the database, and returns `Grant` objects with provider, account, host, owner, and sharing information.

**Call relations**: The sandbox execution environment calls this when preparing grant-related command-line environment values for an agent run.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 325–337)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the actor is allowed to do it. Revoking the edge stops that agent from using the connection but does not necessarily delete the underlying connection.

**Data flow**: It receives a grant id and the member attempting the action. It checks permission through `_grant_for_actor`; if no valid grant is found it returns false, otherwise it deletes the grant row and returns whether a row was removed.

**Call relations**: This method delegates the ownership and admin checks to `_grant_for_actor` before doing the delete, so permission logic stays consistent with other grant changes.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 339–396)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Attaches an already existing connection to the current agent. This lets a member reuse a connection they own, or use a connection that has been shared with the workspace.

**Data flow**: It receives provider, account id, conversation id, actor member id, and a requested shared flag. It finds the connection, refuses private connections owned by someone else, refuses attempts to widen sharing through attach, and inserts the agent grant if it is not already present. It returns true if the connection existed and was attachable, or false if no such connection was found.

**Call relations**: This is used when no new OAuth handoff is needed because the connection already exists. It relies on database uniqueness rules so repeated attaches do not create duplicate grant edges.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 398–426)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the underlying connection is shared with the workspace. This controls whether other members may attach that connection to agents.

**Data flow**: It receives a grant id, the desired shared value, and the actor member id. It checks whether the actor may change the grant’s connection, then updates the connection’s shared flag and timestamp. It returns whether the update happened.

**Call relations**: It uses `_grant_for_actor` for the permission check. Admins are allowed only for narrowing access in this path, not for widening someone else’s private connection.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 428–484)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Deletes a connection entirely after checking permission. It also cleans up related source data so the workspace does not keep treating disconnected provider content as live.

**Data flow**: It receives a connection id and actor member id. It verifies the actor may mutate the connection, finds sources tied to it, removes source grants, detaches and marks those sources as removed, tombstones their pages, and deletes the connection row. It returns true if the connection was found and removed, or false if it was not found.

**Call relations**: It calls `_connection_for_actor` for the ownership/admin decision, then performs the larger cleanup. Grant rows tied to the connection are expected to disappear through database cascading.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 486–514)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific connection. It is the shared guardrail for connection-level mutations.

**Data flow**: It receives an open database connection, a connection id, an actor member id, and whether admins are allowed. It locks and reads the connection row, returns none if missing, returns the id if the actor owns it, otherwise checks whether the actor is an admin and either returns the id or raises a permission error.

**Call relations**: `GrantStore.disconnect` uses this directly. `_grant_for_actor` also uses it after translating a grant id into its connection id.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 516–526)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Checks whether a workspace member is an admin. Admin status can allow some connection changes when the owner is not the actor.

**Data flow**: It receives an open database connection and a member id. It reads the member row in the current workspace and returns true if the stored admin flag is true, otherwise false.

**Call relations**: `_connection_for_actor` calls this only when the actor is not the connection owner and admin authority might matter.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 528–566)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific grant belonging to the current agent. It connects grant-level actions back to the underlying connection’s permission rules.

**Data flow**: It receives an open database connection, grant id, actor member id, and whether admins are allowed. It finds the grant’s connection for the current workspace and agent, asks `_connection_for_actor` whether the actor may touch that connection, then re-reads and locks the grant row. It returns the grant id if valid, or none if the grant is gone or not for this agent.

**Call relations**: `GrantStore.revoke` and `GrantStore.set_shared` call this before making changes. It hands off connection ownership checks to `_connection_for_actor`.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 584–604)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Creates the provider authorization URL that a member should open in their browser. It packs all needed context into a sealed state value so the later callback can be trusted without a temporary pending row.

**Data flow**: It receives workspace, agent, provider, grantor member, conversation, and sharing choice. It finds the provider descriptor, builds a `ConnectState`, encrypts it with Fernet, and asks the provider to build the authorization URL. The returned value is the browser link.

**Call relations**: This is the first leg of the OAuth flow. `ConnectHandoff.authorize` calls it when a terminal connect request needs a fresh URL.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 606–611)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks that a provider name can be connected before a request is accepted. This catches unknown providers early.

**Data flow**: It receives a provider string. It returns silently if the provider is in the fixed provider map or the resolver claims it; otherwise it raises an unknown-provider error.

**Call relations**: This is a validation step for connect requests before the later, memoized authorization URL is created.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 613–618)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Performs a quick local check that the connect machinery for a provider is still installed. It avoids slower external validation while a turn row is locked.

**Data flow**: It receives a provider string and returns true if it is in the installed provider map or if an open resolver exists. It does not call out to external catalogs.

**Call relations**: `ConnectHandoff.authorize` uses this while preparing or reusing a private authorization URL for a terminal connect request.


##### `ConnectFlow.bridge_workspace`  (lines 620–626)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to run under. This protects bridge traffic from being pointed at the wrong workspace or callback.

**Data flow**: It receives a sealed state value, provider name, and callback URL. It opens the state, checks that the provider and callback match what was sealed and configured, confirms the provider exists, and returns the workspace id. If anything does not match, it raises an invalid-state error.

**Call relations**: `connect_bridge_workspace` calls this through the installed flow when handling connector browser bridge requests.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 628–644)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth handoff after the provider redirects back with a code. It turns the code into a connected account and records the grant for the intended agent.

**Data flow**: It receives the sealed state and returned code. It opens the state, finds the provider descriptor, enters the sealed workspace and agent context, exchanges the code for an account, records the connection and grant in `GrantStore`, and returns a `GrantRecorded` summary.

**Call relations**: This is the second leg of the OAuth flow. It calls `_open` and `_provider`, then hands the durable database work to `GrantStore.record`.

*Call graph*: calls 2 internal fn (_open, _provider); 3 external calls (__init__, agent, ws).


##### `ConnectFlow._provider`  (lines 646–652)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth provider descriptor for a provider name. It hides whether the provider came from a fixed map or an open resolver.

**Data flow**: It receives a provider name. It returns the matching descriptor from the installed map, asks the resolver to build one if available, or raises an unknown-provider error.

**Call relations**: `authorize`, `bridge_workspace`, and `complete` all call this before they can build URLs, verify bridge state, or exchange codes.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 654–659)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. This is how the callback proves the state was created by this server recently and was not tampered with.

**Data flow**: It receives the state string from the browser redirect. It decrypts it with a time limit, converts the JSON into a `ConnectState`, and returns that object. If decryption fails or the state is too old, it raises an invalid-state error.

**Call relations**: `bridge_workspace` and `complete` call this at the start of their work, before trusting any provider, workspace, agent, or member information from the request.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 668–750)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a private terminal connect request into a reusable OAuth authorization URL for the right member. It prevents another member from using the request and avoids minting multiple URLs for the same turn.

**Data flow**: It receives workspace id, turn id, and member id. It locks the turn row, checks that the terminal connect request still exists, belongs to that member, names a known provider, and has not expired. If a still-valid URL is already stored, it returns it; otherwise it calls `ConnectFlow.authorize`, saves the URL and timestamp, and returns the saved or memoized URL.

**Call relations**: This sits between a user-facing surface and `ConnectFlow.authorize`. It is the gatekeeper that binds one terminal request to one speaking member and one short-lived authorization link.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 756–764)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow object. This gives tools and callback handlers one shared place to find the configured OAuth machinery.

**Data flow**: It receives a `ConnectFlow` or none and stores it in a module-level variable. It returns nothing, but future calls to `installed_connect_flow` see the new value.

**Call relations**: Server startup uses this to register the configured providers, key, store, and callback URL. Tests can also replace it with a stub flow.


##### `installed_connect_flow`  (lines 767–770)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed connect flow or fails loudly if credential grants are not configured. This prevents code from silently pretending OAuth grants are available.

**Data flow**: It reads the module-level installed flow. If present, it returns it; if missing, it raises `ConnectUnavailable`.

**Call relations**: `connect_bridge_workspace` calls this before verifying bridge requests. Other parts of the system can use it as the central access point for the configured flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 773–782)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming browser bridge request is valid and, if so, identifies its workspace. Invalid requests are rejected by returning none.

**Data flow**: It reads `state`, `provider`, and `callback` query parameters from the request. It asks the installed connect flow to verify them and returns the workspace id on success; if the flow is unavailable, the provider is unknown, or the state is invalid, it returns none.

**Call relations**: This is the safe wrapper around `ConnectFlow.bridge_workspace` for request-handling code. It turns expected verification failures into a simple accept-or-reject result.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 789–798)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. The name is short enough for system limits but still includes a digest so similar account ids do not collide.

**Data flow**: It receives provider and account id strings. It slugifies both into lowercase dash-separated text, computes a short SHA-256 digest from the exact provider/account pair, truncates the readable part to leave room for the digest, and returns the final name.

**Call relations**: It calls `_slug` for readable pieces and is used wherever the same connection or grant needs to be named consistently across user surfaces.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 801–802)

```
def _slug(raw: str) -> str
```

**Purpose**: Converts free-form text into a simple lowercase slug. A slug is a URL- and object-name-friendly string made mostly of letters, numbers, and dashes.

**Data flow**: It receives raw text, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims leading and trailing dashes, and returns the cleaned string.

**Call relations**: `account_object_name` uses this for both provider names and account ids before adding the collision-resistant digest.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 805–812)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of the current agent’s connector grants. This is useful for showing what outside accounts the agent can currently use.

**Data flow**: It reads the current workspace and agent from context, builds a database filter for just that agent’s grants, and delegates the actual query and row conversion to `_grant_summaries`.

**Call relations**: This is the agent-scoped public helper. It shares the common summary-building work with `workspace_grant_summaries` through `_grant_summaries`.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, agent_current, ws_current).


##### `workspace_grant_summaries`  (lines 815–818)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of all connector grants in one workspace. This is meant for an operator or admin view rather than one agent’s local view.

**Data flow**: It receives a workspace id, enters that workspace context, builds a workspace-wide grant filter, and delegates the database query to `_grant_summaries`.

**Call relations**: This is the workspace-wide companion to `grant_summaries`. Both use `_grant_summaries` so the returned row shape stays consistent.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 821–861)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into readable summary records. It joins grants with their connections and agent names.

**Data flow**: It receives a database filter describing the desired scope. It queries connector grants joined to connection and agent tables, orders the rows by provider and agent name, and returns `GrantSummary` objects with account, owner, conversation, timestamps, and sharing information.

**Call relations**: `grant_summaries` and `workspace_grant_summaries` call this after deciding the scope. It centralizes the common reporting query.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 3 external calls (__init__, select, workspace_tx).


##### `connection_summaries`  (lines 864–926)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the member-owned connections in the current workspace, along with the agents that have grants to each one. This shows connections independently from the currently bound agent.

**Data flow**: It reads the current workspace, queries connections with optional joined grant and agent rows, groups rows by provider and account id, collects agent names, and returns `ConnectionSummary` objects with sorted agent lists.

**Call relations**: This supports workspace or portal views that need to show connected accounts and who can use them, rather than only showing one agent’s active grants.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### Workspace connector objects
Exposes connected accounts and agent permissions through the workspace object system.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter, “treat this folder as an importable package.” That matters because the project’s connector extension code likely lives in nearby files under this directory, and other parts of the system need to import it using package-style names. Think of it like putting a label on a drawer: the label does not contain the tools, but it lets people find and refer to the drawer reliably. Because this file is empty, importing `ufo_ext_connectors` does not run setup code, define shared objects, or change program state. Its purpose is structural: it makes the package boundary explicit and keeps imports working in environments that expect an `__init__.py` file.


### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `workspace object reads and edits during portal use or tool turns`

A connected account is sensitive: it may belong to one member, may be usable by one or more agents, and may involve outside services. This file turns that into two clear workspace object types. A `connection` is the real member-owned account connection, created only through the separate `connect_account` flow because a third party is involved. A `connector_grant` is one agent’s access to that connection, like a keycard issued for one door.

The file defines small data shapes for these objects, then supplies stores that know how to list them, show their details, report their live status, and apply allowed changes. The stores read summaries from the grants layer, which is the part of the system that knows about connected credentials and permissions. They wrap those rows in object-system records with owners, timestamps, summaries, and links.

The important safety rule is that creation and editing are limited. You cannot create a connection by writing an object; you must use the connection flow. Deleting a connection disconnects it for every agent. A grant can be attached to an existing connection, have its `shared` flag changed under strict rules, or be revoked without deleting the underlying connection. The two `ObjectKind` values at the bottom register these behaviors with the wider workspace object system.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This is part of a small contract saying that a summary row must expose the name of the outside provider, such as a service or platform. It exists so helper code can treat connection summaries and grant summaries the same way.

**Data flow**: There is no real runtime work here; it is a promised property. Any row used with the shared naming helper must already contain a provider value, and the helper can then read that value safely.

**Call relations**: The `_named` helper relies on this property when it builds stable object names. The property is not called as a normal implemented function here; it describes what compatible summary objects must provide.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This is the other half of the summary-row contract: each row must expose the account identifier at the outside provider. Together with the provider name, it uniquely names the account for object display.

**Data flow**: There is no stored state or transformation in this protocol property. A compatible row comes in with an account ID already available, and callers can read that ID when they need to build a name.

**Call relations**: The `_named` helper uses this alongside `_AccountSummary.provider` so both connection rows and grant rows can be converted into the same human-readable object-name format.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper turns a group of account-like summary rows into a dictionary keyed by the project’s standard account object name. It avoids duplicating the naming rule in both connection and grant listing code.

**Data flow**: It receives a tuple of rows, each with a provider and account ID. For each row, it asks `account_object_name` to make the official name, then returns a dictionary from that name to the original row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this when they are preparing lists for the object system. It hands them a ready-made name-to-row mapping so they can build object list entries.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–83)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists connected accounts as workspace object rows. Each row says what the account is and who owns it, so the object system can show and gate it correctly.

**Data flow**: It asks the grants layer for connection summaries, converts each provider/account pair into a stable object name, and builds `OwnedRow` records. Each output row includes a short summary and an owner record containing the owning member and the connection generation ID.

**Call relations**: The member-readable object framework calls this when it needs to list `connection` objects. It uses `_named` for consistent names, `connection_summaries` for source data, and hands `OwnedRow` objects back to the framework.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 85–103)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This loads the detailed view of one connected account. It fills in the account’s specification and timestamps if the connection still exists.

**Data flow**: It receives an object name and owner metadata, then fetches current connection summaries and searches for the row whose ID matches the owner’s generation. If found, it returns an `ObjectDetail` containing the provider, account ID, creation time, and update time; if not found, it returns nothing.

**Call relations**: The object system calls this after a specific `connection` row has been selected. It depends on `connection_summaries` for fresh data and returns a `ConnectionSpec` wrapped in object detail form.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 105–118)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This reports live, operational information about a connection that is not part of the editable object specification. It is useful for seeing who owns the connection, where it is hosted, and which agents currently have access.

**Data flow**: It receives the tool context, object name, and owner metadata. It looks up the current connection by generation ID, then returns a small JSON-like dictionary with owner member ID, host, and agent names; if the row disappeared, it returns nothing.

**Call relations**: The object framework calls this when a status view is requested for a `connection`. It reads from `connection_summaries` and hands back simple values that can be shown to a user or agent.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 120–128)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses attempts to create or modify a real account connection through normal object apply. Connecting an account requires a separate consent flow with a third party, so object editing is not allowed for this.

**Data flow**: It receives the requested connection spec and any previous object state, but it does not use them to change anything. Instead, it raises a `VerbNotSupported` error with a message telling the caller to use `connect_account`.

**Call relations**: The object system calls this when someone tries to apply a `connection` object. Rather than handing off to the grants layer, it stops the action immediately to protect the consent-based connection process.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 130–140)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account when its owner or an allowed admin deletes the `connection` object. Deleting here is broad: it removes the account connection and therefore cuts it off from every agent.

**Data flow**: It receives the tool context, object name, and owner metadata. It checks that the grants service is available and that there is a speaking member to act as the person making the change, then asks the grants layer to disconnect the connection generation. If the grants layer says the row was no longer current, it raises an error.

**Call relations**: The object framework calls this during deletion of a `connection`. After the object-level permission gate has allowed the attempt, this method hands the actual disconnect request to `ctx.grants.disconnect`.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 151–152)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This answers one narrow permission question: whether a workspace admin is allowed to apply a grant change. Admins are only allowed to turn a shared grant private, not share it or change its account details.

**Data flow**: It receives the old grant spec and the requested new spec. It compares the new spec with a copy of the old one where only `shared` has been changed to `False`, and returns true only when that exact change is being made from a currently shared grant.

**Call relations**: The member-readable object framework uses this as part of deciding whether an admin may apply a `connector_grant` update. It uses `ConnectorGrantSpec.model_copy` to express the one allowed edit cleanly.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 154–171)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists agent access grants as workspace object rows. Each row represents one agent’s permission to use a connected account and says whether that permission is shared or private.

**Data flow**: It asks the grants layer for grant summaries, names each provider/account pair with the shared naming helper, and builds `OwnedRow` records. Each output row includes a readable summary, the owner member ID, the shared flag, and the grant generation ID.

**Call relations**: The object framework calls this when listing `connector_grant` objects. It uses `grant_summaries` as the source of truth, `_named` for consistent naming, and returns rows the framework can filter and display.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 173–214)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This builds the detailed object view for one connector grant. It shows what account the grant refers to, whether it is shared, and how it links to the agent and possibly the underlying connection.

**Data flow**: It receives the object name and owner metadata, then looks up the current grant row by generation ID. If found, it returns an `ObjectDetail` with provider, account ID, shared flag, timestamps, and links: always a `scoped_to` link to the agent, and for private grants an `access_to` link to the underlying connection object.

**Call relations**: The object system calls this when a specific `connector_grant` needs detail. It reads from `grant_summaries`, uses `account_object_name` when it needs to point at the connection, and creates `ObjectLink` and `ObjectRef` records for navigation.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 216–230)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This reports live status for one connector grant. It lets a caller see who owns the underlying permission, what host it belongs to, which agent holds it, and whether it is shared.

**Data flow**: It receives context, name, and owner metadata, then fetches current grant summaries and finds the matching generation. If the grant exists, it returns a JSON-like dictionary with owner member ID, host, agent, and shared flag; otherwise it returns nothing.

**Call relations**: The object framework calls this for status requests on `connector_grant` objects. It does not change anything; it simply reads `grant_summaries` and formats the relevant fields.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 232–280)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This applies allowed changes to an agent’s account grant. It can attach an existing connection to the current conversation as a new grant, or change only the grant’s shared/private setting when that is permitted.

**Data flow**: It receives the requested grant spec, any old spec, and owner metadata. If this is a new grant, it requires a grants service and speaking member, then asks the grants layer to attach the existing provider account to the current conversation. If editing an existing grant, it reloads the current row, refuses account/provider changes, does nothing when the shared flag is unchanged, and otherwise asks the grants layer to update the shared flag.

**Call relations**: The object system calls this when applying a `connector_grant`. It uses `grant_summaries` to verify the current state, raises `VerbNotSupported` for attempts that would really be connection creation or account switching, and hands valid attach or sharing changes to `ctx.grants.attach` or `ctx.grants.set_shared`.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 282–292)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected account. It does not delete the underlying connection and does not remove other agents’ grants.

**Data flow**: It receives the tool context, object name, and owner metadata. It checks that the grants service is available and that a speaking member is present, then asks the grants layer to revoke the grant generation. If the grant changed or disappeared before revocation, it raises an error.

**Call relations**: The object framework calls this when deleting a `connector_grant`. After object-level ownership rules allow the delete, this method passes the real revocation work to `ctx.grants.revoke`.


### Live connector tools
Lets agents discover and invoke tools from external connectors and workspace-configured MCP servers.

### `extensions/connectors/ufo_ext_connectors/tools.py`

`domain_logic` · `tool invocation / request handling`

A connector broker is like a reception desk for many outside services. The agent cannot keep a fixed list of every possible Slack, GitHub, or Gmail action, because those catalogs are large and can change. This file solves that by exposing four general tools: list connectors, describe a connector's tools, search a connector's tools, and call one tool.

When a tool is called, the file first looks up the current turn's connector registry, which is the live list of connected providers. Discovery results are kept small enough to stay readable in the model's context. If a search finds nothing, it falls back to the connector's top tools and clearly says those are not relevance-ranked matches, so the agent does not mistake silence for impossibility.

Execution is careful because it crosses trust boundaries. If an argument points at a workspace file, the file is uploaded from inside the sandbox rather than through the server process. Files returned by a connector are downloaded back into a safe workspace folder. Base64 data, which is a text encoding often used to carry file bytes inside JSON, is decoded: small text is shown inline, while large or binary content is written to a workspace file. Slack sends get a small “Sent using ufo” attribution so text posted into someone else's Slack surface is marked.

#### Function details

##### `list_external_tools`  (lines 254–277)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Finds available connector providers, such as Slack or GitHub, that match the user's keywords. This is the first discovery step before asking what a connector can actually do.

**Data flow**: It receives the tool context and search queries. It reads the turn's connector registry, checks local provider names and labels, also asks the broker catalog for matches, removes duplicates, and returns a JSON result containing connector IDs and labels.

**Call relations**: When the agent needs to know which outside services are available, this function starts the search. It relies on _registry to get the live connector list and finishes through _json_result so the answer is returned in the standard tool-result shape.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 280–302)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector and, when needed, discovers nearby tools by query. It prevents the agent from guessing tool names that may not exist.

**Data flow**: It takes a connector ID, optional exact tool names, and an optional search query. It asks the broker for schemas for exact names, records any names that were not found, searches or lists tools when needed, and returns schemas plus available alternatives as JSON.

**Call relations**: After a connector is found, this is the normal next step before execution. It uses _registry to find the connector, _tool_json to make broker tools readable, _discovery_query when guessed names fail, _discovered_rows to keep discovery useful, and _json_result to return the final answer.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 305–309)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes ufo attribution text from a Slack message that is being read back. This helps distinguish a real user mention from a footer the system previously wrote.

**Data flow**: It receives message text. It applies the attribution pattern everywhere it could appear and returns the cleaned text, without changing anything else.

**Call relations**: This is a small helper for Slack-related reading behavior. It uses the same attribution rules as the sending path, but in reverse: instead of adding a footer, it removes known footers before interpreting the message.


##### `attributed_arguments`  (lines 312–339)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a Slack attribution footer to outgoing message arguments when there is a message body to attach it to. It avoids adding a second footer if one is already present.

**Data flow**: It receives a dictionary of connector arguments and the attribution subject text. It checks whether the arguments already carry attribution, builds a Slack context footer, appends it to existing blocks or converts text-style arguments into blocks, and returns the updated arguments or the original arguments if no safe change is possible.

**Call relations**: slack_attributed calls this only for Slack message-send tools. It delegates the details to _carries_attribution, _appended_blocks, and _body_blocks so each Slack input shape is treated correctly.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 342–370)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns a Slack message body supplied as plain text or markdown text into Slack block objects that can be followed by the attribution footer.

**Data flow**: It reads the message fields inside the arguments. Markdown text becomes one markdown block, while plain Slack mrkdwn text is split into section blocks that respect Slack's per-block size limit. If there is no usable body, it returns nothing.

**Call relations**: attributed_arguments uses this when the caller did not already provide a blocks array. It prepares the body in the right Slack format before the footer block is appended.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 373–394)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Appends an attribution footer to an existing Slack blocks value. It supports blocks passed either as a real list or as serialized JSON text.

**Data flow**: It receives the existing blocks value and a footer block. If the blocks are a non-empty list, it returns a new list with the footer added. If they are a JSON string, possibly URL-encoded, it parses the list, checks for existing attribution, appends the footer, and serializes it back in the same style. If it cannot safely understand the value, it returns nothing.

**Call relations**: attributed_arguments calls this for messages that already use Slack's blocks field. It calls _carries_attribution to avoid stacking footers and uses JSON and URL encoding helpers to preserve the caller's original representation.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 397–406)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a value already contains the ufo attribution footer. This protects against adding duplicate footers to Slack messages.

**Data flow**: It receives any JSON-like value. Strings are searched directly; lists and dictionaries are searched recursively; other values are treated as not carrying attribution. It returns true or false.

**Call relations**: Both attributed_arguments and _appended_blocks consult this before modifying Slack message content. It is the shared “never stack the footer” guard.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 409–423)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether a connector call is a Slack message send that needs ufo attribution. Non-Slack calls and Slack actions that are not sending messages are left untouched.

**Data flow**: It receives the provider name, tool slug, and arguments. It checks whether the provider is Slack and whether the tool name looks like a message send, post, reply, or schedule action. If so, it returns arguments with attribution added; otherwise it returns the original arguments.

**Call relations**: call_external_tool runs arguments through this just before execution. When attribution is required, this function hands the detailed rewrite to attributed_arguments.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 426–431)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real connector tool on behalf of the connected account. This is the execution doorway after the agent has discovered the connector and tool schema.

**Data flow**: It receives a connector ID, tool name, optional account ID, and arguments. It finds the connector entry, resolves which connected account to use, adds Slack attribution when appropriate, creates a _ConnectorCall, and returns the serialized execution result as tool content.

**Call relations**: This is the public execution handler used by the connector tool definition. It uses _registry for lookup, ToolContext.connector_account for account selection, slack_attributed for Slack send safety, and _ConnectorCall.run for the full server-side execution flow.

*Call graph*: calls 3 internal fn (connector_account, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 459–472)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Performs a connector execution from start to finish. It stages input files, calls the broker, pulls output files back, decodes bulky encoded content, and shortens repeated result objects.

**Data flow**: It receives tool arguments and an account ID. Each argument is walked so workspace-file references can be staged for upload. The broker is then called with the staged arguments. Any broker file outputs are fetched into the workspace, the JSON response is translated, optional workspace file references are added, and the final payload is deduplicated into a JSON string.

**Call relations**: call_external_tool creates a _ConnectorCall and calls this method. The method coordinates the private steps: _staged_value before execution, _fetched_files and _translated_node after execution, and _deduped in a worker thread at the end.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 474–489)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks an argument value and replaces any workspace-file reference with the broker's upload-ready argument. This lets connector tools receive files without the server process copying the bytes itself.

**Data flow**: It receives one argument value. If the value is exactly a workspace-file marker, it validates the path and stages that file. If the value is a dictionary or list, it recursively rewrites children. Other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before broker execution. When it finds a file marker, it hands the actual upload preparation to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 491–527)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker's file store in the safe way expected by connector tools. It checks the file, enforces size limits, asks the broker for an upload URL, and performs the upload from inside the sandbox.

**Data flow**: It receives a workspace path. It normalizes the path, runs a sandboxed preflight program to hash and size the file, rejects unreadable or oversized files, guesses a content type, asks the broker to stage the upload, optionally sends the bytes with curl, and returns the argument object the broker wants for that file.

**Call relations**: _staged_value calls this whenever it finds a workspace-file argument. It uses sandbox helpers and broker upload staging so file bytes move through the sandbox and broker storage, not through ordinary tool-result text.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 529–562)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector into the workspace. It gives each fetch a fresh safe path so returned files do not overwrite existing workspace content.

**Data flow**: It receives broker file records containing names and download URLs. For each file, it reduces the provider's name to a safe filename, claims a new workspace path, downloads the file with curl under the size limit, and returns a list of names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using file output records extracted by the broker. Its output is added to the final result under the workspace-files key.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 564–624)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Translates base64-encoded fields inside one result object into readable text or workspace-file references. This keeps huge unreadable base64 blobs out of the agent's context.

**Data flow**: It receives a dictionary node from the broker response. It first recursively translates child values, then looks for provider markers saying fields are base64. Valid base64 fields are decoded, named, typed, and passed to _translated_bytes. Marker fields are updated only when the translation is complete enough to be truthful.

**Call relations**: _ConnectorCall.run starts result translation here, and _translated calls it for nested dictionaries. It relies on _decoded_base64 to safely decode and _translated_bytes to decide whether decoded content stays inline or becomes a file.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 626–645)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any result value that may contain encoded content. It handles dictionaries, lists, and standalone data URLs.

**Data flow**: It receives a value and the current nesting depth. Deep values beyond the safety limit are returned unchanged. Dictionaries go to _translated_node, lists are walked item by item, and short data URLs with base64 payloads go to _translated_data_url. Other values pass through unchanged.

**Call relations**: _translated_node uses this to walk child fields. It is the general recursive path, while _translated_node and _translated_data_url handle the two specific base64 shapes.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 647–659)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a standalone data URL that carries base64 content. A data URL is a string that includes both a media type and the file bytes inside the string itself.

**Data flow**: It receives a string starting with data:. If the string matches the expected base64 data-URL format, it decodes the payload, chooses a filename extension from the declared media type, and passes the bytes onward. If parsing or decoding fails, it returns the original string unchanged.

**Call relations**: _translated calls this for possible data URLs. It uses _decoded_base64 for validation and _translated_bytes for the final inline-versus-file decision.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 661–670)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the tool result. Small readable UTF-8 text stays inline; large text and binary data are saved as workspace files.

**Data flow**: It receives raw bytes, optional decoded text, a filename, and a media type. If the text exists and is under the inline limit, it returns that text. Otherwise it writes the bytes through _offloaded and returns a file reference object.

**Call relations**: _translated_node and _translated_data_url both call this after successful base64 decoding. It is the shared choice point between readable result text and workspace storage.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 672–714)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded bytes into the workspace and returns a reference to the saved file. This is used when decoded connector content is too large or not safe to show inline.

**Data flow**: It receives a proposed name, media type, and byte content. It creates a safe filename, builds a content-addressed path using a SHA-256 hash, writes a temporary file through the sandbox, atomically places it at the final path, and returns metadata including name, workspace path, media type, and byte count.

**Call relations**: _translated_bytes calls this when content should not be inlined. It works with sandbox writing and containment checks so predictable paths cannot be abused with symlinks or unsafe overwrites.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 716–774)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Shrinks a JSON result by replacing repeated large objects with pointers to their first copy. This helps useful results stay inline instead of being pushed out to a file because of repeated boilerplate.

**Data flow**: It receives the final payload dictionary. It serializes the payload and skips deduplication if it is too large, too structurally dense, or already uses the reserved pointer key. Otherwise it walks top-level values through _condensed and returns a JSON string with later repeated objects replaced by same_as references.

**Call relations**: _ConnectorCall.run calls this at the end in a worker thread because walking a large JSON shape can take noticeable CPU time. It uses _escaped to build JSON Pointer paths and _condensed to find repeated structures.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 776–852)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one JSON-like node and identifies repeated dictionary objects by structure. When it sees a large dictionary that has already appeared, it replaces it with a pointer to the first occurrence.

**Data flow**: It receives a value, its JSON Pointer path, the current depth, and a table of first-seen object fingerprints. It recursively processes dictionaries and lists, builds strong hashes from child content, estimates original size, records large first occurrences, and returns the rewritten value plus its fingerprint and size.

**Call relations**: _deduped uses this as the workhorse for result condensation. It calls _escaped when descending through object keys and uses SHA-256 hashes so two different provider objects are not treated as the same.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 855–858)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path segment for a JSON Pointer. JSON Pointer is a standard way to name a location inside a JSON document.

**Data flow**: It receives a dictionary key string. It replaces ~ and / with their pointer-safe spellings and returns the escaped token.

**Call relations**: _deduped and _condensed use this when building same_as paths. Without it, keys containing slashes or tildes could point to the wrong part of the result.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 861–885)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider claims is base64. It refuses oversized, invalid, or non-string values rather than guessing.

**Data flow**: It receives any value. If the value is a string within the decode limit, it removes whitespace, strictly base64-decodes it, and tries to decode the bytes as UTF-8 text. It returns bytes plus text when possible, bytes plus no text for binary data, or nothing if decoding is not valid.

**Call relations**: _translated_node and _translated_data_url call this before turning encoded content into text or files. It is deliberately bounded because the decode operations can block the main Python interpreter while they run.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 888–902)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs richer tool discovery inside one connector using a natural-language goal. It returns matching tools plus broker guidance such as plans or pitfalls.

**Data flow**: It receives a connector ID and query. It finds the connector, asks the broker's search API for matching tools and advice, formats the tool rows with _discovered_rows, adds any fallback or omission note, and returns everything as JSON.

**Call relations**: This is a public discovery handler alongside describe_external_tools. It uses _registry for the connector lookup, _discovered_rows for the shared fallback-and-budget behavior, and _json_result for the tool response.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 905–908)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Returns the current turn's connector registry, or fails clearly if connector tools were invoked without one. The registry is the live map of available connector providers for this turn.

**Data flow**: It receives the tool context. If the context has a connector registry, it returns it. If not, it raises an error explaining that connector dispatch was set up incorrectly.

**Call relations**: All public connector handlers call this before looking up providers. It is the common guard that keeps discovery and execution tied to the current turn's actual connected services.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 911–912)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Turns a broker tool object into the small JSON shape returned to the agent. It exposes the slug, description, and input schema.

**Data flow**: It receives one broker tool record. It copies the fields the agent needs to choose and call the tool into a plain dictionary and returns it.

**Call relations**: describe_external_tools uses this for exact schema results, and _available_tools uses it for discovery lists. It keeps tool formatting consistent across both paths.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 915–932)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the visible discovery rows for connector tools and adds an explanatory note when search falls back. This makes sure a failed query still gives the agent a useful next step.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If a non-empty query found nothing, it asks for the connector's unfiltered top tools instead. It trims the listing with _available_tools and returns the rows plus notes about fallback or omitted entries.

**Call relations**: describe_external_tools and search_connector_tools both use this shared seam. That means both discovery routes follow the same “never a dead end, but label fallback honestly” rule.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 935–948)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Limits a connector tool listing so it fits in the inline result budget. This avoids returning a giant catalog that would be less useful to the agent.

**Data flow**: It receives a tuple of broker tools. It converts each to JSON, counts the serialized size as it goes, stops once the budget is exceeded after at least one row, and returns the rows that fit.

**Call relations**: _discovered_rows calls this after either search results or fallback top tools are chosen. It relies on _tool_json so each row has the same shape as described schemas.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 951–958)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Chooses the query used for tool discovery when exact tool names failed. It turns guessed slugs into useful search words.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If an explicit query exists, it returns that. Otherwise it lowercases the unresolved names, replaces punctuation with spaces, removes duplicate words while preserving order, and returns the resulting search phrase.

**Call relations**: describe_external_tools calls this when it needs to search after unresolved exact tool names or when discovery is requested. It helps recover from guessed names by searching for their meaningful words.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 961–962)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary payload as the standard text-based tool result. Connector discovery responses all leave this file through this helper.

**Data flow**: It receives a payload dictionary. It serializes the dictionary to JSON text, wraps that text in a TextContent object, wraps that in a ToolResult, and returns it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools call this to produce consistent responses. call_external_tool builds its result directly because _ConnectorCall.run already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

This file is the bridge between the agent and external MCP servers. Without it, the agent would only know about tools built into this project; it could not ask a workspace’s own MCP server what tools exist or run one of those tools.

The file exposes two agent tools. The first, `list_mcp_tools`, asks a named MCP server for its tool catalog. To avoid flooding the agent with huge tool definitions, it first returns a compact catalog: tool names, short summaries, parameter names, required parameters, and whether a tool appears safe to repeat. If the agent chooses specific tools, it can ask again and get their full input schemas.

The second tool, `call_mcp_tool`, runs one named MCP tool with JSON arguments. Before sending the request, it checks that the arguments are not too large. After the server replies, it checks that the result is not too large either. This matters because MCP servers are outside systems, and their output is treated as untrusted content.

Workspace MCP servers are configured through a credential slot called `mcp_servers`, which maps friendly names to URLs and optional bearer tokens. The file validates those URLs, builds an HTTP client for the chosen server, and uses FastMCP to do the protocol details such as session setup and paginated tool listing.

#### Function details

##### `McpServer._http_url`  (lines 77–80)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validator makes sure a configured MCP server address starts with `http://` or `https://`. It prevents the extension from trying to connect to unsupported or surprising kinds of addresses.

**Data flow**: It receives the `url` string from workspace configuration. It checks the string against the allowed web-address pattern. If the address is valid, the same string comes out; if not, validation fails with a clear error.

**Call relations**: This is used automatically when a `McpServer` is built from the workspace’s `mcp_servers` credential. It protects later steps, such as `_server` and `mcp_client`, by ensuring they only receive HTTP-style server URLs.


##### `mcp_client`  (lines 115–121)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This creates a FastMCP client connected to one configured MCP server. It is the small doorway through which tool listing and tool calling both talk to the outside server.

**Data flow**: It receives a validated `McpServer` object containing a URL and possibly an auth token. It turns the token into an `Authorization: Bearer ...` HTTP header when present, builds a streamable HTTP transport, and returns a client with a 30-second timeout.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` has found the right server configuration. The returned client then performs the actual MCP operations, while FastMCP takes care of lower-level protocol work such as initialization, session IDs, and streaming frames.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 124–136)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up one named MCP server from the workspace’s saved credentials. It is the gatekeeper that makes sure tool requests only go to servers the workspace actually configured.

**Data flow**: It receives the current tool context and a server name. It reads the `mcp_servers` credential value, parses and validates it into named server records, then returns the matching `McpServer`. If there is no extension context, no valid credential, or no server with that name, it raises an error instead of guessing.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` start by asking `_server` to resolve the user-provided server name. Once `_server` returns a safe, validated server record, those functions pass it to `mcp_client` to contact the external MCP endpoint.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 139–160)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing `list_mcp_tools` tool. It lets the agent discover what a configured MCP server can do, first in a compact browsing view and then, when requested, with full schemas for selected tools.

**Data flow**: It receives a tool context and parsed input containing the server name and optional tool names. It resolves the server, opens an MCP client, asks the server for its tools, and then chooses the response shape. With no tool names, it turns every tool into a compact catalog entry. With tool names, it checks that they exist and returns full schema entries, refusing overly broad schema requests that would not fit usefully.

**Call relations**: This function is registered in `manifest` as the handler for `list_mcp_tools`. It relies on `_server` to find configuration, `mcp_client` to talk to the MCP server, `_catalog_entry` for compact results, `_schema_entry` for full tool definitions, `_bounded_schemas` for size-aware schema output, and `_json_result` to package the final answer.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 163–165)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This reads whether an MCP tool is marked as idempotent, meaning it should be safe to repeat without causing a new side effect each time. For example, reading a document is usually idempotent; sending an email usually is not.

**Data flow**: It receives one MCP tool description. It looks at the tool’s annotations, and if an `idempotentHint` is present, it returns that as a boolean. If there are no annotations, it returns `False`.

**Call relations**: `_catalog_entry` and `_schema_entry` call this so both compact catalog entries and full schema entries include the same safety hint. That hint helps the agent understand whether repeating a tool call is likely to be harmless.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 168–184)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns a full MCP tool definition into a small, easy-to-scan catalog item. It helps the agent choose which tools are worth inspecting further without dumping every full JSON schema at once.

**Data flow**: It receives an MCP tool object. It reads the tool name, description, input schema properties, required fields, and idempotency annotation. It outputs a JSON-friendly dictionary containing the name, a short summary, sorted parameter names, sorted required parameter names, and the idempotent flag.

**Call relations**: `_list_mcp_tools` calls this when the agent asks to browse a server’s catalog without naming specific tools. `_catalog_entry` calls `_summary` to shorten the description and `_idempotent` to include the repeat-safety hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 187–193)

```
def _summary(description: str) -> str
```

**Purpose**: This extracts a short summary from a longer tool description. It keeps catalog browsing readable by using only the opening line and trimming it to a fixed length.

**Data flow**: It receives a description string. It strips leading and trailing whitespace, takes the first line, keeps only the text before the first sentence break, and limits it to the maximum summary length. The result is a short plain string.

**Call relations**: `_catalog_entry` calls this while building the compact tool catalog. It prevents long MCP descriptions, which may include argument documentation, from overwhelming the first browsing response.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 196–202)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns an MCP tool definition into the detailed form the agent needs before calling that tool. It includes the full input schema so the agent can use the server’s exact parameter names and types.

**Data flow**: It receives an MCP tool object. It copies out the tool name, full description, input schema, and idempotent flag. It returns those as a JSON-friendly dictionary.

**Call relations**: `_list_mcp_tools` calls this when the agent asks for full definitions of specific tool names. It calls `_idempotent` so the detailed schema response carries the same repeat-safety hint as the catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 205–217)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing `call_mcp_tool` tool. It runs a specific tool on a configured MCP server and converts the server’s response into the project’s normal tool-result format.

**Data flow**: It receives a tool context and parsed input containing the server name, tool name, and JSON arguments. It resolves the server, checks that the serialized arguments are no larger than the request byte limit, opens an MCP client, and calls the remote tool. If the MCP server reports an error, it returns an error tool result with bounded text. If the server returns structured JSON content, it returns that. Otherwise, it joins text blocks from the response and returns them as JSON.

**Call relations**: This function is registered in `manifest` as the handler for `call_mcp_tool`. It depends on `_server` for configuration lookup, `mcp_client` for the network connection, `_joined_text` to read text-only MCP responses, `_bounded` to enforce response size limits, and `_json_result` to package successful JSON output.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 220–221)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This collects plain text blocks from an MCP response into one string. It is used when a server did not provide structured JSON content or when an error message needs to be shown.

**Data flow**: It receives a list of response content blocks. It keeps only blocks that are MCP text content, takes their text fields, and joins them with newline characters. The result is one readable string.

**Call relations**: `_call_mcp_tool` calls this after a remote tool call. It gives `_call_mcp_tool` a simple text fallback when the MCP response is not structured JSON, and it also helps build readable error output.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 224–227)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for text returned through this extension. It fails loudly instead of silently cutting off content, because partial tool output can mislead the agent.

**Data flow**: It receives a string and measures its size in bytes after encoding. If the text is within the response limit, it returns the same string. If it is too large, it raises `McpError`.

**Call relations**: `_call_mcp_tool` uses this for error text from failed MCP calls, and `_json_result` uses it for serialized JSON responses. This makes size checking happen right next to the data being returned.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 230–247)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This decides whether a requested set of full tool schemas is small enough to return usefully. It prevents the agent from receiving a huge, truncated group of schemas when it should instead ask for fewer tools.

**Data flow**: It receives a JSON-style payload and the number of requested tools. If more than one schema was requested and the serialized payload is too large for the normal listing limit, it raises a clear error telling the agent to ask for fewer schemas. Otherwise, it passes the payload to `_json_result` and returns the resulting tool result.

**Call relations**: `_list_mcp_tools` calls this after building full schema entries for named tools. `_bounded_schemas` then hands acceptable payloads to `_json_result`, while blocking multi-schema requests that would be too large to be useful.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 250–251)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This wraps a JSON-friendly dictionary as a normal tool result. It is the shared final packaging step for successful MCP listing and calling responses.

**Data flow**: It receives a dictionary that can be represented as JSON. It serializes it to a JSON string, checks the string with `_bounded`, places that text into a `TextContent` object, and returns a `ToolResult` containing it.

**Call relations**: `_list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` all use this when they need to return successful JSON data to the agent. It centralizes the size check and result formatting so those callers do not each do it differently.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 254–285)

```
def manifest() -> Manifest
```

**Purpose**: This describes the extension to the host system: its name, version, available tools, and required credential slot. It is how the rest of the project learns that this MCP tool pack exists.

**Data flow**: It takes no input. It builds a `Manifest` containing two tool definitions, `list_mcp_tools` and `call_mcp_tool`, each with its description, input model, handler function, and untrusted-content marking. It also declares the `mcp_servers` credential slot and explains the expected JSON shape for configured servers.

**Call relations**: The extension loader calls `manifest` when it wants to register this file’s capabilities. The returned manifest connects user-visible tool names to `_list_mcp_tools` and `_call_mcp_tool`, and tells the host that `_server` will need access to the `mcp_servers` credential.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Evaluation connectors
Provides deterministic fake connector services that exercise the same brokered access path as production.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `import time / evaluation setup`

This file does not contain executable code. Its main job is to identify this folder as a Python package and to state, in one short module comment, what the package is for. The package represents a deterministic evaluation environment: a controlled test world where mailbox and calendar data can be supplied by fake connector providers instead of real email or calendar systems. This matters because real services are unpredictable. Messages can change, network calls can fail, and calendars depend on outside accounts. For evaluation, the project needs repeatable behavior, like using a practice stage instead of a live theater. By keeping these fake providers in their own package, the rest of the system can import them when it needs stable, predictable inputs for testing or benchmarking.


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `startup registration and eval connector calls`

This file gives evaluation runs a small, controlled world for an agent to act in: a mailbox, a calendar, and a code search service. The point is not to mock the connector system, but to place a predictable service behind the real connector doorway. Like a practice kitchen for a chef, the tools behave enough like the real thing that the agent must use the normal steps, but the ingredients and final state are fully checkable by the test.

The file declares three connector providers: eval email, eval calendar, and eval code search. Email and calendar data live in database tables tied to a workspace, so one evaluation cannot accidentally see another evaluation’s messages or events. Sending an email inserts a row into the sent folder. Listing mail reads rows back, optionally filtered by text. Calendar tools create, list, update, and cancel events, with cancelled events kept visible rather than deleted.

Code search is different. It is read-only. Instead of building search results from tables, evaluations seed the exact response bytes under a query key. When the agent searches, the broker returns that seeded response verbatim, and fails loudly if nothing was seeded. Finally, the manifest function registers these providers with simple OAuth stubs so the extension can be discovered and called through the normal system.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Creates a database transaction for this extension’s workspace-scoped storage. The email and calendar tools use it whenever they need to read or write durable evaluation state.

**Data flow**: It takes no direct input. It builds an extension context using this extension’s name and no declared credentials, then returns a transaction object that callers can enter before running database queries. The result is a safe database connection scope for one operation.

**Call relations**: The email and calendar helper methods call this just before touching their tables. It supplies the shared storage doorway used by sending mail, listing mail, creating events, listing events, and changing events.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a Python datetime object. If the input does not include a time zone, it treats it as UTC, the standard world clock time.

**Data flow**: A text timestamp goes in. The function parses it, checks whether it has time zone information, adds UTC if it does not, and returns a datetime object ready to store in the database.

**Call relations**: Event creation and event updating call this before saving start and end times. This keeps calendar rows using real datetime values instead of raw text.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one eval provider, optionally filtered by a search phrase. This is how the connector system can ask, “What can this provider do?”

**Data flow**: It receives a workspace id, provider name, and search text. It looks up that provider’s catalog, filters by tool slug or description if a search phrase was supplied, and returns matching tool descriptions. If nothing matches, it returns the full catalog instead of an empty set.

**Call relations**: The broker’s search method calls this when the connector system searches available tools. It reads from the in-file catalog and does not touch the database.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the detailed description and input shape for one tool. Callers use it when they know a tool name and need to know what arguments it accepts.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans that provider’s catalog and returns the matching tool definition. If the slug is not known, it raises an error that says the broker tool is unknown.

**Call relations**: This is part of the normal connector flow after tools are discovered. It relies on the shared catalog and signals bad tool names with the standard unknown-tool error.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs the requested eval tool. It is the main dispatcher that turns a provider name, tool name, and raw arguments into the correct email, calendar, or code-search action.

**Data flow**: It receives the workspace, provider, tool slug, argument values, account id, and optional idempotency key. It validates the arguments against the right input model, calls the matching private helper, and returns that helper’s result as a dictionary. If the provider or tool is not recognized, it raises an unknown-tool error.

**Call relations**: This is the central call path used when the connector system actually invokes a tool. It hands work off to the specific helpers for sending or listing mail, creating, listing, updating, or cancelling events, and searching code.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the exact code-search response that an evaluation seeded for a query. It deliberately fails if the fixture is missing, so a broken test setup is noticed immediately.

**Data flow**: A validated code-search query goes in. The function looks in this extension’s scoped store under a key made from a fixed prefix plus the query. If the stored value is a dictionary, it copies and returns it. If not, it raises an error explaining that no fixture was seeded.

**Call relations**: The execute dispatcher calls this for the code search provider’s search_code tool. Unlike email and calendar helpers, it reads from the scoped key-value store rather than the database tables.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Records a sent email in the eval mailbox. This gives tests a durable row they can later inspect to see what the agent sent.

**Data flow**: It receives a workspace id and validated email fields: recipients, subject, and body. It creates a new id, opens a transaction, inserts a row into the email table as a sent message from the fixed assistant address, and returns the new id, status, and recipient list.

**Call relations**: The execute dispatcher calls this for the send_email tool. It uses the shared transaction helper and writes to the eval email table.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the eval mailbox, newest first. It can narrow the results to one folder and an optional text search.

**Data flow**: It receives a workspace id plus folder, query text, and limit. It builds database conditions for the workspace and folder, adds a case-insensitive sender/subject/body search if requested, reads matching rows, and returns them as simple email dictionaries.

**Call relations**: The execute dispatcher calls this for the list_emails tool. It uses the transaction helper to read from the email table and formats rows for the connector response.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Adds a confirmed event to the eval calendar. Tests can then check that the agent scheduled the right thing.

**Data flow**: It receives a workspace id and validated event details. It generates an event id, parses the start and end time strings, inserts a confirmed event row with attendees, and returns the new event id and confirmed status.

**Call relations**: The execute dispatcher calls this for the create_event tool. It uses _moment to normalize times and _transaction to write the database row.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Reads calendar events for a workspace in start-time order. It can optionally filter by event title.

**Data flow**: It receives a workspace id plus query text and limit. It builds a database query for that workspace, adds a title filter if needed, reads matching event rows, converts each row into response-friendly JSON, and returns the event list.

**Call relations**: The execute dispatcher calls this for the list_events tool. It reads through the transaction helper and uses _event_json so event output has one consistent shape.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares changes for an existing calendar event. It only changes fields the caller actually supplied.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary from any provided title, start time, end time, or attendees, parsing time strings when present. If there is nothing to change, it raises an error. Otherwise it passes the changes to the shared event-changing helper and returns the updated event.

**Call relations**: The execute dispatcher calls this for update_event. This method does the argument-to-change-set work, then hands the database update to _change_event.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled. It does not delete the event, so later listings can still show that it was cancelled.

**Data flow**: It receives a workspace id and an event id. It creates a small change saying the status should become cancelled, sends that to the shared event-changing helper, and returns the updated event data.

**Call relations**: The execute dispatcher calls this for cancel_event. It reuses _change_event, the same helper used by event updates.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the fresh event record. It is the shared database update path for both editing and cancelling events.

**Data flow**: It receives the workspace id, event id text, and a dictionary of fields to change. It converts the event id to a UUID, updates only the matching row in that workspace, checks that exactly one row changed, reads the updated row back, and returns it as an event dictionary. If no matching event exists, it raises an error.

**Call relations**: _update_event and _cancel_event call this after deciding what should change. It uses the transaction helper, performs the database update and readback, and formats the result through _event_json.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Turns a database event row into the plain dictionary shape returned by calendar tools. This keeps event responses consistent across listing, updating, and cancelling.

**Data flow**: A database row goes in. The function converts the id and times to strings, keeps the title, attendees, and status, and returns a dictionary suitable for a connector response.

**Call relations**: Event listing and event changing both call this before returning event data. It is a small formatting step between database rows and tool output.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that eval environment tool responses do not produce downloadable files. The connector interface asks brokers about file outputs, and this broker always answers none.

**Data flow**: It receives a response dictionary but does not inspect it. It returns an empty tuple, meaning there are no broker files attached to the response.

**Call relations**: This supports the wider broker interface. No helper in this file calls it, but connector code can use it after tool execution to ask whether a response created files.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for these eval providers. Email, calendar, and code search in this environment accept only structured arguments, not uploaded files.

**Data flow**: It receives upload details such as workspace, provider, tool, filename, type, and checksum. Instead of staging anything, it raises an error saying these providers accept no file uploads.

**Call relations**: This is present because the broker interface includes upload support. If the wider connector flow ever tries to upload a file for an eval tool, this method stops it immediately.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery results in the standard broker search response. It lets the connector system search for available eval tools using the same shape as other providers.

**Data flow**: It receives a workspace id, provider name, and query text. It asks tools for the matching catalog entries, then places those tools into a BrokerSearch object and returns it.

**Call relations**: This is a thin bridge from the connector search interface to the broker’s tools method. It does not read or write evaluation state.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple synthetic credential for an eval account. This satisfies code paths that expect a bearer token, without contacting a real service.

**Data flow**: It receives a workspace id, provider name, and account id. It builds a credential whose bearer token is the fixed eval prefix plus the account id, and returns it.

**Call relations**: This supports the wider connector authentication flow. The eval tools themselves do not need a real external token, but the connector interface still asks the broker for one.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a pretend OAuth authorization URL for the eval provider. OAuth is the standard web flow where a user grants access to an account.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider’s fake host into an authorization URL string and returns that string.

**Call relations**: The manifest attaches this OAuth object to each connector provider. Evaluations usually seed grants directly, but this method keeps the provider descriptor complete if the connect flow is inspected.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the pretend OAuth exchange by returning the fixed eval account. It does not validate a real authorization code because evals do not contact outside services.

**Data flow**: It receives a code, redirect URI, workspace id, and state. It ignores the external-service details and returns an OAuth account with the fixed eval account id.

**Call relations**: The connector registry can call this if it drives the OAuth exchange. It returns the account object expected by the normal connector setup path.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest that advertises the eval email, calendar, and code-search providers. This is the entry point the extension system uses to discover what this package offers.

**Data flow**: It creates one EvalEnvBroker, creates three connector provider records with labels, OAuth stubs, and that shared broker, then returns a Manifest containing the extension name, version, and providers.

**Call relations**: This runs when the extension is loaded. It wires together _EvalEnvOAuth, ConnectorProvider, and EvalEnvBroker so later connector calls can discover schemas and execute tools.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).

# Credential Grants and Egress Policy  `stage-22.1`

This stage is behind-the-scenes safety gear for any sandbox that needs to use outside services. A sandbox is the isolated place where an agent runs. Before it can call an external website or use a private account, this stage checks what is allowed and which secrets may be used.

credentials.py is the vault. It asks users for API keys or account details, stores them encrypted, and only reveals them to trusted parts of the system when needed. grants.py is the permission desk. It records when a workspace member connects an outside account, lets an agent use it, shares or lists that access, and later revokes or disconnects it.

egress_resolver.py is the rule brain. It looks at the workspace, the agent, stored grants, and credentials to decide which destinations are allowed and what secret headers or tokens may be attached. egress_control.py is the doorway used by the Rust egress proxy. The proxy asks it for a decision, while the Python service keeps the sensitive policy, credentials, and billing records centralized and protected.

## Files in this stage

### Credential and Grant Management
Workspace secrets and outside-account connections are stored, protected, granted, shared, revoked, and disconnected.

### `core/src/ufo/access/credentials.py`

`domain_logic` · `cross-cutting: used during startup setup, credential prompts, provider callbacks, sandbox opening, and proxy rule derivation`

This file is the project’s safe deposit box for bring-your-own-key secrets. A workspace may need an API token or provider installation to reach outside services. The code here makes sure those values are not written into chat, logs, or plain database rows. Instead, values are encrypted with Fernet, which is a symmetric encryption tool where the same private key locks and unlocks the data.

The file has two main jobs. First, it creates sealed requests. A sealed request is like a signed permission slip: it says which workspace, member, and credential slot a private prompt is allowed to fill. When the value comes back, the seal is checked so a secret cannot be written to the wrong workspace or wrong slot. Similar seals bind external provider installations, so a guessed installation ID cannot be reused by another workspace.

Second, it stores and retrieves encrypted slot values in the database. Consumers do not read raw database rows directly. They ask helpers such as `slot_secret`, `slot_is_set`, and `credential_host`, which combine stored values, provider-minted short-lived secrets, and declared host choices into one consistent answer. This matters because the sandbox, proxy, and extension rules must all agree on which credential exists and where it may be sent.

#### Function details

##### `deploy_env`  (lines 29–35)

```
def deploy_env(name: str) -> str | None
```

**Purpose**: Looks up a deployment-level secret from environment variables. It first checks a UFO-specific name, then the ordinary upstream name, so the project can keep its own secrets separate when needed while still working in standard environments.

**Data flow**: It receives a secret name such as `ANTHROPIC_API_KEY`. It checks the process environment for `UFO_ANTHROPIC_API_KEY`, then `ANTHROPIC_API_KEY`, treating an empty value as missing. It returns the found string or `None` if no usable value exists.

**Call relations**: This is a small standalone helper used wherever deployment configuration needs to read a platform secret. It does not call other project code; it only reads the process environment.


##### `credential_object_name`  (lines 38–41)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a safe, simple object name. This gives different parts of the system a shared way to refer to the same credential slot.

**Data flow**: It receives a slot name. It lowercases it, replaces runs of non-letter-or-number characters with hyphens, and trims extra hyphens from the ends. It returns the cleaned name.

**Call relations**: When `named_slots` needs stable names for declared credential slots, it calls this function first. This function delegates the text replacement to Python’s regular expression library.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `named_slots`  (lines 44–59)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Builds the public names used to address declared credential slots. If two slots would clean up to the same name, it adds a short stable fingerprint so both can still be addressed safely.

**Data flow**: It receives a tuple of declared slot objects. It groups them by the cleaned name from `credential_object_name`. Single slots keep the plain cleaned name; colliding slots get a short hash based on extension and slot name. It returns a dictionary from final object name to the original slot.

**Call relations**: This is used when the system needs one agreed naming scheme for reading and deleting credential objects. It calls `credential_object_name` for the ordinary name and uses SHA-256 hashing to make collision suffixes predictable.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 103–104)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Encrypts a credential request state into an opaque text token. The token can later prove what workspace, member, slot, and purpose were approved without exposing those details as editable plain text.

**Data flow**: It receives a Fernet encryption object and a `CredentialRequestState`. It converts the state to JSON, encrypts those bytes, and returns the encrypted text string.

**Call relations**: `CredentialRequests.seal`, `CredentialRequests.authorize`, and `seal_installation` all call this when they need to create a sealed permission slip. It hands the actual locking work to Fernet.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 107–133)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Decrypts and checks a sealed credential token. It rejects tokens that were tampered with, expired, malformed, or sealed for the wrong purpose.

**Data flow**: It receives a Fernet object, sealed text, an expected purpose, and optionally a time limit. It decrypts the token, parses the JSON into a request state, checks the purpose, and returns the state. If anything is wrong, it raises `CredentialRequestInvalid`.

**Call relations**: `CredentialRequests.open_authorization`, `authorized_slot_workspace`, and `open_installation` call this before trusting any sealed data. It centralizes the dangerous part so callers do not each need to remember every safety check.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 152–165)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request for member-entered credential values. It only seals slots that are installed and allowed to be typed by a member.

**Data flow**: It receives a workspace ID, member ID, and slot names. It checks that every slot is declared, then checks that every slot is fillable by a user. If the checks pass, it builds a request state and returns its encrypted seal.

**Call relations**: This is used before a private credential prompt is shown. It calls `seal_credential_request` to produce the token that the later fulfillment must present.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 167–180)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization token for a provider flow, such as an OAuth-style callback. It records the workspace, member, slot, and provider state so the callback can be matched to the original request.

**Data flow**: It receives a workspace ID, member ID, one slot, and a provider payload. It verifies the slot is declared and the payload is not empty. It then seals those facts into encrypted text and returns it.

**Call relations**: This is used when a member starts an external provider authorization. It calls `seal_credential_request`, and the resulting seal is later opened by `CredentialRequests.open_authorization` or inspected by `authorized_slot_workspace`.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 182–196)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Opens a provider authorization seal and proves it belongs to the exact workspace, member, and slot expected. It returns the provider state that was sealed inside.

**Data flow**: It receives sealed text plus the expected workspace ID, member ID, and slot. It decrypts the seal, checks each expected field, verifies the slot is declared, and returns the payload. If any field does not match, it raises an error.

**Call relations**: This is called when a provider authorization response is being completed. It relies on `open_credential_request` for the decryption and basic purpose check, then adds the more specific identity and slot checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 199–213)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Seals a provider installation ID so it is bound to one workspace and one slot. This prevents a user from typing or guessing a raw installation ID and making it look legitimate.

**Data flow**: It receives a Fernet object, workspace ID, slot, and installation ID. It creates a credential request state marked with the installation-binding purpose, puts the installation ID in the payload, encrypts it, and returns the seal.

**Call relations**: Provider integration code uses this when an external installation has been approved. It calls `seal_credential_request`, using a different purpose from ordinary member credential prompts.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 216–227)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed installation binding and returns the installation ID only if it belongs to the expected workspace and slot.

**Data flow**: It receives a Fernet object, workspace ID, slot, and sealed text. It decrypts the seal without an expiration time, checks that it is an installation binding for the same workspace and slot, and returns the payload installation ID. Invalid or mismatched seals raise `CredentialRequestInvalid`.

**Call relations**: Provider credential sources call this kind of logic when they need to use a stored installation. It relies on `open_credential_request` for decryption and purpose checking, then verifies the workspace and slot.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 233–239)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Installs the process-wide credential request authority. This gives routes that do not have normal request context, such as browser provider callbacks, a way to open credential seals.

**Data flow**: It receives a `CredentialRequests` object or `None`. It stores that value in a module-level variable. Nothing is returned, but later calls can read the installed authority.

**Call relations**: Startup code uses this once the credential key and declared slots are known. Later, `installed_credential_requests` and `authorized_slot_workspace` depend on this stored value.


##### `installed_credential_requests`  (lines 242–245)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority. It fails clearly if credential authorization was not configured.

**Data flow**: It reads the module-level installed authority. If one exists, it returns it. If not, it raises a runtime error explaining that no credential key is configured.

**Call relations**: Code that needs to complete provider authorization can call this instead of passing the credential authority through every layer. It depends on `install_credential_requests` having run earlier.


##### `authorized_slot_workspace`  (lines 248–264)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Figures out which workspace a provider authorization seal belongs to, but only if the seal matches the exact slot and payload expected. It returns `None` instead of throwing for invalid callback data.

**Data flow**: It receives sealed text, a slot, and a payload. It uses the installed Fernet authority to open the seal, checks that the slot and payload match, and returns the workspace ID. If no authority is installed or the seal is invalid or mismatched, it returns `None`.

**Call relations**: A provider callback route can use this when the browser returns with no normal session or turn context. It calls `open_credential_request` to verify the seal before trusting it.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 271–293)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a credential value for a workspace and slot, encrypted before it reaches the database. It updates an existing slot or inserts a new one.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects empty values, encrypts the secret, opens a database transaction, tries to update the existing row, and inserts a row if none was updated. It returns nothing but changes the credential table.

**Call relations**: Credential fulfillment and provider binding code use this after a value has been safely approved. It uses `workspace_tx` for the database transaction and SQLAlchemy to build the update and insert statements.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 295–307)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Retrieves and decrypts a stored credential value. It reports a missing slot with a specific `CredentialSlotUnset` error.

**Data flow**: It receives a workspace ID and slot. It queries the credential table for encrypted bytes, raises `CredentialSlotUnset` if no row exists, decrypts the stored value if found, and returns the plaintext string.

**Call relations**: `slot_secret`, `slot_is_set`, `credential_host`, and GitHub extension credential sources call this when they need a stored value. It uses `workspace_tx` for the database read and SQLAlchemy to build the select.

*Call graph*: called by 7 (credential_host, slot_is_set, slot_secret, bound, secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 309–340)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if its current decrypted value still matches an expected old value. This prevents two refresh operations from accidentally overwriting each other.

**Data flow**: It receives a workspace ID, slot, expected current secret, and replacement secret. It rejects an empty replacement, reads the current encrypted row, decrypts it, compares it to the expected value, and only then writes the new encrypted value. It returns `true` if the update happened and `false` if the row was missing, changed, or not updated.

**Call relations**: OAuth-style credential sources use this after refreshing a token with an external provider. It uses `workspace_tx` and SQLAlchemy for the read-and-update sequence.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 364–369)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host-choice declaration is internally valid. The default host must be one of the allowed hosts.

**Data flow**: After a `HostChoice` is created, it compares the default host against the tuple of allowed hosts. If the default is missing, it raises a value error. Otherwise the object remains usable.

**Call relations**: This runs automatically when a `HostChoice` is constructed. It protects later code such as `credential_host` from receiving a declaration with an impossible fallback.


##### `HostChoice.resolve`  (lines 371–376)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string. It accepts different capitalization from the stored value but never returns an undeclared host.

**Data flow**: It receives the selected text. It trims spaces, lowercases it for comparison, searches the declared host list case-insensitively, and returns the canonical declared host. If there is no match, it returns `None`.

**Call relations**: `credential_host` uses this when a workspace has stored a host choice. This keeps the proxy and sandbox tied to the extension’s closed list of safe hosts.


##### `CredentialSource.secret`  (lines 384–384)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the interface for a credential source that can mint a secret for a workspace. “Mint” here means create or fetch a usable secret from a provider instead of reading a member-typed value directly.

**Data flow**: An implementation receives a workspace ID and credential store. It may read stored binding information, contact its provider, and return a secret string, or return `None` if nothing can be minted.

**Call relations**: `slot_secret` calls this when a slot declares a provider-backed source. This function is a protocol method, so the real behavior lives in implementing classes such as extension credential sources.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 386–394)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the interface for checking whether a provider-backed slot has enough stored binding to mint a secret, without actually minting it. This avoids expensive provider calls when the system only needs to know whether a credential exists.

**Data flow**: An implementation receives a workspace ID and credential store. It checks local binding state or other cheap information and returns `true` or `false`; it may raise if a binding exists but cannot be used safely.

**Call relations**: `slot_is_set` calls this during checks such as sandbox setup. Like `CredentialSource.secret`, this is a protocol method implemented by provider-specific code.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 397–412)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the main question: what secret should this slot use for this workspace? It prefers a provider-minted secret when available, otherwise falls back to the encrypted stored value.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and store. If a source exists, it asks the source for a secret and returns it if present. If not, it tries to read the stored value. A missing stored slot becomes `None` rather than an exception.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers use this shared path so they all agree on the credential value. It calls `CredentialSource.secret` for provider-backed slots and `CredentialStore.get` for stored slots.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 415–433)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a slot would provide a secret, without actually producing that secret. This is useful when opening a sandbox and deciding whether to configure a client at all.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and store. If a source exists and says it is bound, it returns `true`. Otherwise it tries to read the stored slot. A missing stored value returns `false`; a found value returns `true`.

**Call relations**: Sandbox and rule setup code use this cheaper existence check. It calls `CredentialSource.bound` instead of minting a provider token, and falls back to `CredentialStore.get` for ordinary stored credentials.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 436–453)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Resolves the host that a credential is allowed to be sent to. The host may be fixed by code or chosen by the workspace from a declared safe list.

**Data flow**: It receives the store, workspace ID, and either a plain host string or a `HostChoice`. A plain string is returned directly. For a `HostChoice`, it reads the workspace’s stored selection; if none exists it returns the default host, and if a stored value does not match the allowed list it returns `None`.

**Call relations**: The egress proxy and sandbox export logic use this so both sides agree on the same destination host. It calls `CredentialStore.get` for stored host choices and uses `HostChoice.resolve` behavior to keep choices inside the declared set.

*Call graph*: calls 1 internal fn (get).


### `core/src/ufo/access/grants.py`

`domain_logic` · `request handling`

This file is the project’s permission desk for connected accounts. A member may start an OAuth flow, leave the app to approve access on another service, and return with proof that the account was connected. This code seals the important details into a short-lived state token, checks that token on the way back, asks the provider to exchange the returned code for a broker-side account, then stores a connection row and a grant row saying which agent may use it.

The design keeps secrets on the server side. The agent gets a usable edge to the connection, not the provider token itself. Think of it like giving an employee a badge that opens one door, not handing them the building’s master key.

The file also enforces ownership rules. A connected account belongs to one member in a workspace. Other agents can be attached only when the owner allows it or the connection is shared. Admins can do some cleanup, but cannot secretly widen private access. When a connection is removed, related feed sources are stopped and their pages are tombstoned so stale synced data is no longer treated as live.

Finally, it provides summary views for user interfaces and operator tools, plus small helpers for stable names, callback verification, and the process-wide installed connect flow.

#### Function details

##### `grant_sentinel`  (lines 49–53)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a predictable placeholder credential name for a connected account. The sandbox and the outgoing proxy can both recognize the same account without sharing a separate registry.

**Data flow**: It receives an account id string, prefixes it with a fixed marker, and returns the combined string. It does not read or change stored state.

**Call relations**: This is a small helper used wherever the system needs the grant to appear as a CLI-style credential while still mapping back to the broker-held account.


##### `UnknownProvider.__init__`  (lines 61–62)

```
def __init__(self, provider: str) -> None
```

**Purpose**: Creates a clear, member-facing error when someone asks for a connector provider that is not installed or claimed. It avoids exposing only an internal slug with no explanation.

**Data flow**: It receives the provider name, formats a sentence saying no connector is available, and stores that as the exception message.

**Call relations**: Provider lookup code raises this when validation or descriptor lookup fails, so the connect request stops before creating a useless authorization link.

*Call graph*: called by 2 (_provider, validate_provider).


##### `OAuthProvider.provider`  (lines 105–105)

```
def provider(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose its stable provider name. This is the name stored with connections and grants.

**Data flow**: An implementation returns a string identifying the provider. The protocol itself only describes the expected shape.

**Call relations**: ConnectFlow reads this from the provider descriptor after a successful OAuth exchange so the recorded connection uses the descriptor’s canonical name.


##### `OAuthProvider.host`  (lines 108–108)

```
def host(self) -> str
```

**Purpose**: Defines that every provider descriptor can expose the provider host allowed for outgoing traffic. This helps the proxy know what outside destination the grant admits.

**Data flow**: An implementation returns a host string, or possibly an empty string for brokered providers that do not grant direct host access. The protocol does not store anything itself.

**Call relations**: ConnectFlow passes this value to GrantStore.record when landing a connection, so later egress decisions can match the grant to the correct provider host.


##### `OAuthProvider.authorize_url`  (lines 110–110)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a provider builds the browser URL where a member approves access. The URL carries sealed state so the callback can be trusted later.

**Data flow**: It takes a sealed state string and callback URL, and returns the provider-specific authorization URL. Implementations decide the exact URL format.

**Call relations**: ConnectFlow.authorize calls this after preparing the sealed state, handing the resulting link back to the user interface.


##### `OAuthProvider.exchange`  (lines 112–114)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a provider turns the callback code into a connected broker account. This is where the provider confirms the account that was actually authorized.

**Data flow**: It receives the returned code, callback URL, workspace id, and state; it talks to the provider or broker; it returns an OAuthAccount containing the stable account id and optional display label.

**Call relations**: ConnectFlow.complete calls this after opening the sealed state. Its result becomes the connection that GrantStore.record stores.


##### `OAuthProviderResolver.claims`  (lines 125–125)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how an open provider namespace says whether it can serve a provider slug. This lets a broker support providers that were not individually registered in the static provider map.

**Data flow**: It receives a provider name, may check an external catalog, and returns true or false. It does not itself record a connection.

**Call relations**: ConnectFlow.validate_provider uses this when a provider is not in the installed map, so typos fail before the user is sent to a dead consent page.


##### `OAuthProviderResolver.descriptor`  (lines 127–127)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Defines how an open namespace builds an OAuth provider descriptor for a validated provider slug. The descriptor gives ConnectFlow the same interface as a statically installed provider.

**Data flow**: It receives a provider name and returns an OAuthProvider object for that name. The protocol leaves the construction details to the resolver implementation.

**Call relations**: ConnectFlow._provider calls this when the provider is not in the explicit provider map but a resolver exists.


##### `ConnectionHooks.fire`  (lines 234–234)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Defines the hook called after a connection is durably recorded. Extensions use it to create or refresh state that follows from the new account, such as feed sources.

**Data flow**: It receives a ConnectionRecorded payload describing the landed connection and performs extension-specific work. It returns no useful value.

**Call relations**: ConnectFlow.complete calls this after GrantStore.record succeeds and before the conversation is resumed, so follow-on state can exist before the user sees the success message.


##### `ConnectResumption.resume`  (lines 247–254)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Defines how the system tells the original conversation that the connect request succeeded. This lets the agent continue without waiting for the member to ask again.

**Data flow**: It receives a conversation id, a message, the speaking member id, and an idempotency key that prevents duplicate resumes. It returns a boolean saying whether the resume was actually queued or delivered.

**Call relations**: ConnectFlow.complete calls this last, after the connection and derived hooks have finished, so the resumed turn sees a ready-to-use connection.


##### `_resume_key`  (lines 257–272)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a stable duplicate-prevention key for one OAuth connect attempt. It uses the sealed state, not the connection id, because several different attempts can land on the same connection row.

**Data flow**: It receives the sealed state string, hashes it with SHA-256, keeps a short digest prefix, adds a fixed prefix, and returns the idempotency key.

**Call relations**: ConnectFlow.complete uses this key when resuming the conversation, so a callback refresh repeats the same message safely while a later connect attempt gets its own key.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 299–300)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently active in the request context. GrantStore uses this so database writes and reads stay inside the correct workspace.

**Data flow**: It reads the current workspace context and returns its workspace id. It does not accept inputs or change data.

**Call relations**: Most GrantStore methods rely on this property when filtering or inserting database rows, keeping grant operations scoped to the active workspace.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 303–304)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent currently targeted by object dispatch. This tells GrantStore which agent a grant should belong to.

**Data flow**: It reads the current object-agent context and returns that agent id. It does not write anything.

**Call relations**: GrantStore.record, active_grants, attach, revoke, and sharing checks use this value to bind or find grants for the intended agent.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 306–470)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None, landed_turn_id: UUID | None=None) ->
```

**Purpose**: Stores the result of a successful OAuth connection and grants the current agent access to it. It also wakes up parked feed sources that may now be able to sync again.

**Data flow**: It receives provider, account, owner, conversation, sharing, label, and optional turn information. Inside one database transaction it creates or reuses the connection, refuses to take over an account owned by another member, upserts the agent grant, stamps the asking turn if provided, clears retry blocks on relevant sources, and returns the connection id.

**Call relations**: ConnectFlow.complete calls this after the provider exchange succeeds. Other parts of the system then see the connection row, the grant edge, and any released sources as one settled database change.

*Call graph*: 9 external calls (__init__, now, and_, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 472–517)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the connected accounts that the current agent can use. This is the agent’s practical view of its granted connections.

**Data flow**: It reads connector grants for the current workspace and agent, joins them to connection and member records, and returns Grant objects with provider, account, host, owner, and sharing details.

**Call relations**: The sandbox environment builder calls this when preparing agent execution, so it can expose only the credentials that the agent has actually been granted.

*Call graph*: called by 1 (_grant_cli_env); 4 external calls (__init__, and_, select, workspace_tx).


##### `GrantStore.revoke`  (lines 519–531)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the actor is allowed to do so. It does not delete the underlying member-owned connection.

**Data flow**: It receives a grant id and actor member id. It first checks that the grant exists for the current agent and that the actor owns or may administer the connection, then deletes the grant row and returns whether a row was removed.

**Call relations**: It delegates permission checking to _grant_for_actor before doing the delete, so revocation follows the same ownership rules as sharing changes.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 533–590)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Gives the current agent access to an already existing connection. This is allowed only if the actor owns the connection or the connection is already shared across the workspace.

**Data flow**: It receives provider, account id, conversation id, actor id, and a requested sharing flag. It finds the connection, checks access, refuses attempts to widen sharing through attach, inserts the grant if absent, and returns true; if no connection exists, it returns false.

**Call relations**: This is used when a member wants an agent to use a connection that already exists instead of starting a new OAuth flow. It writes the same grant table that record writes after a fresh connection.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 592–620)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the connection behind a grant is shared with the workspace. It protects private connections by checking who is acting before changing the flag.

**Data flow**: It receives a grant id, the desired shared value, and actor member id. It verifies the actor through _grant_for_actor, updates the related connection’s shared flag, and returns whether the update happened.

**Call relations**: It relies on _grant_for_actor and _connection_for_actor for permission rules. Admin authority is allowed for narrowing access but not for widening someone else’s private connection.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 622–678)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a member-owned connection when the actor is allowed to do it. It also disables the feed sources and pages that depended on that connection.

**Data flow**: It receives a connection id and actor member id. It checks permission, finds sources tied to the connection, removes source grants, marks sources removed, tombstones their pages, deletes the connection, and returns true; if no allowed connection is found, it returns false.

**Call relations**: This uses _connection_for_actor for the ownership/admin check. Deleting the connection also lets database cascade rules remove grant edges.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 680–708)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a connection. Owners are allowed, and admins may be allowed depending on the caller’s rule.

**Data flow**: It receives a database connection, connection id, actor id, and an admin_allowed flag. It locks and reads the connection, returns its id if allowed, returns none if missing, or raises a permission error if the actor has no right to mutate it.

**Call relations**: GrantStore.disconnect calls this directly. _grant_for_actor calls it after finding the connection behind a grant, so grant operations inherit the same permission check.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 710–720)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Answers whether a member is an admin in the current workspace. It is a small permission helper.

**Data flow**: It receives a database connection and member id, reads the member row for the current workspace, and returns true if the stored admin flag is true.

**Call relations**: _connection_for_actor calls this only when the actor is not the connection owner and admin access might be accepted.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 722–760)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Finds a grant for the current agent and checks that the actor may change the connection behind it. This prevents someone from altering grants attached to a connection they do not control.

**Data flow**: It receives a database connection, grant id, actor id, and admin_allowed flag. It reads the grant’s connection id, checks that connection through _connection_for_actor, locks and rechecks the grant row, then returns the grant id or none.

**Call relations**: GrantStore.revoke and GrantStore.set_shared call this before deleting a grant or changing sharing, keeping those operations tied to both the current agent and the connection permission rules.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 781–803)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, turn_id: UUID | None=None) -> str
```

**Purpose**: Starts the OAuth handoff by creating the provider URL the member should open. It seals the workspace, agent, provider, member, conversation, and sharing choice into the state carried through the browser redirect.

**Data flow**: It receives the connect request details, finds the provider descriptor, builds a ConnectState object, encrypts it with Fernet, and returns the provider’s authorization URL.

**Call relations**: ConnectHandoff.authorize uses this when it needs a fresh URL for a turn. Later, ConnectFlow.complete opens the same sealed state when the provider redirects back.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 805–810)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a requested provider is available before the system promises a connect flow. This catches missing connectors early.

**Data flow**: It receives a provider name. If the name is in the installed provider map, it returns; otherwise it asks the resolver if one exists; if neither accepts it, it raises UnknownProvider.

**Call relations**: Connect request creation can call this before storing a request, while ConnectHandoff later uses the cheaper knows_provider check when reminting a URL.

*Call graph*: calls 1 internal fn (__init__).


##### `ConnectFlow.knows_provider`  (lines 812–817)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Performs a quick local check that the connect machinery still knows how to serve a provider. It avoids external catalog calls while a turn row is locked.

**Data flow**: It receives a provider name and returns true if the provider is explicitly installed or if an open resolver is present. It does not validate the provider against a live catalog.

**Call relations**: ConnectHandoff.authorize calls this before minting a URL from an existing turn request, making sure the connector has not disappeared.


##### `ConnectFlow.bridge_workspace`  (lines 819–825)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and returns which workspace it is allowed to act for. This protects bridge calls from mismatched provider, callback, or tampered state values.

**Data flow**: It receives state, provider, and callback strings. It opens the sealed state, compares the provider and callback to expected values, confirms a provider descriptor exists, and returns the workspace id or raises an invalid-state error.

**Call relations**: connect_bridge_workspace calls this from an incoming request. If it succeeds, the bridge can safely enter the sealed workspace context.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 827–877)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth handoff after the provider redirects back. It verifies the state, exchanges the provider code, records the grant, notifies extensions, and optionally resumes the conversation that asked for the connection.

**Data flow**: It receives the sealed state and provider code. It decrypts and validates the state, gets the provider descriptor, enters the claimed workspace and agent context, exchanges the code for an account, records the connection and grant, fires connection hooks, sends a success message if resumption is installed, and returns a GrantRecorded summary.

**Call relations**: The OAuth callback path calls this. It hands database work to GrantStore.record, extension work to ConnectionHooks.fire, and conversation wake-up work to ConnectResumption.resume.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 879–884)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns the human-friendly provider name shown to members. If no label was declared, it turns the provider slug into title-cased words.

**Data flow**: It receives a provider string, looks it up in the labels map, and otherwise replaces underscores with spaces and title-cases the result.

**Call relations**: ConnectFlow.complete uses this when composing the success message and GrantRecorded result, so members see a recognizable connector name.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 886–892)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. It hides the difference between explicitly installed providers and providers served through an open resolver.

**Data flow**: It receives a provider name, returns a descriptor from the providers map if present, otherwise asks the resolver for one if installed, and raises UnknownProvider if no path exists.

**Call relations**: ConnectFlow.authorize, bridge_workspace, and complete all call this before provider-specific work, so every leg uses a consistent descriptor lookup.

*Call graph*: calls 1 internal fn (__init__); called by 3 (authorize, bridge_workspace, complete).


##### `ConnectFlow._open`  (lines 894–899)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. It refuses state that was changed, expired, or cannot be read.

**Data flow**: It receives the state string, decrypts it with the configured Fernet key and time limit, parses it as ConnectState, and returns the claims. On failure it raises ConnectStateInvalid.

**Call relations**: ConnectFlow.bridge_workspace and ConnectFlow.complete both call this before trusting anything from a browser callback or bridge request.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 927–1012)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Hands a private OAuth URL to the member who originally requested a connection. It reuses a recent URL when safe, or mints a fresh one when the old state may be too old.

**Data flow**: It receives workspace id, turn id, and member id. It locks and reads the turn, verifies the terminal connect request still exists and belongs to that member, checks the provider and target agent, returns a still-fresh stored URL if available, otherwise calls ConnectFlow.authorize, stores the new URL, and returns it. If another concurrent writer wins, it reads and returns that fresh URL.

**Call relations**: User surfaces call this when drawing or pressing the connect control for a turn. It wraps ConnectFlow.authorize with request ownership checks and memoization.

*Call graph*: calls 1 internal fn (_held); 5 external calls (__init__, model_validate, select, update, workspace_tx).


##### `ConnectHandoff._held`  (lines 1014–1025)

```
def _held(self, url: str | None, authorized_at: datetime | None) -> str | None
```

**Purpose**: Decides whether a previously minted authorization URL is still fresh enough to reuse. It prevents handing out a URL whose sealed state may expire while the member is completing consent.

**Data flow**: It receives an optional URL and timestamp. If either is missing, or if the timestamp is older than the memo window, it returns none; otherwise it returns the URL.

**Call relations**: ConnectHandoff.authorize calls this before minting and again after a race with another writer, so repeated presses usually see the same live URL.

*Call graph*: called by 1 (authorize); 3 external calls (now, replace, timedelta).


##### `install_connect_flow`  (lines 1031–1039)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide ConnectFlow used by tools, surfaces, and callbacks. Passing none disables grants for deployments without the needed credential key.

**Data flow**: It receives a ConnectFlow object or none and stores it in a module-level variable. It returns nothing.

**Call relations**: Startup code calls this once to wire in providers, encryption, storage, callback URL, and hooks. Tests can call it to install a stub flow.


##### `installed_connect_flow`  (lines 1042–1045)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the currently installed ConnectFlow or fails loudly if none was configured. This gives callers one standard way to access the connect machinery.

**Data flow**: It reads the module-level installed flow. If present, it returns it; if absent, it raises ConnectUnavailable.

**Call relations**: connect_bridge_workspace calls this before validating bridge requests. Other entry points can use the same helper instead of carrying the flow through every call.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 1048–1057)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Extracts and verifies the workspace for a connector browser bridge request. It returns none instead of leaking detailed errors to the bridge caller.

**Data flow**: It receives a Starlette request, reads state, provider, and callback query parameters, asks the installed flow to verify them, and returns the workspace id. If validation, availability, or provider lookup fails, it returns none.

**Call relations**: Browser bridge handling can call this at the edge of a request to decide whether to enter a workspace context or reject the request.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 1064–1073)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. It keeps names short and adds a hash suffix so similar account names do not collide.

**Data flow**: It receives provider and account id strings, slugifies both, hashes the exact provider/account pair, truncates the readable head to fit the maximum length, and returns the combined name.

**Call relations**: Surfaces that name connection and connector-grant objects use this so the same account edge is shown consistently across the product.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 1076–1077)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a simple lowercase slug suitable for object names. It removes punctuation-like runs by replacing them with dashes.

**Data flow**: It receives a raw string, lowercases it, replaces non-letter-or-number runs with hyphens, trims outside hyphens, and returns the cleaned string.

**Call relations**: account_object_name calls this for both provider and account id before adding the collision-preventing digest.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 1080–1088)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of connector grants for the current target agent. This is useful for showing what accounts that agent can use.

**Data flow**: It builds a database scope for the current workspace and object-dispatch agent, then delegates the actual query and row conversion to _grant_summaries.

**Call relations**: Agent-facing or object-scoped views call this when they need grant details without writing their own join across grants, connections, members, and agents.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 1091–1094)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of all connector grants in a workspace. This is broader than the current-agent view and is meant for operator or admin surfaces.

**Data flow**: It receives a workspace id, enters that workspace context, and asks _grant_summaries for every grant row in that workspace.

**Call relations**: Workspace-level views call this to reuse the same summary-building logic as grant_summaries, but with a wider scope.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 1097–1148)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into readable GrantSummary records. It joins enough tables to show the agent, provider account, owner email, timestamps, and sharing flag.

**Data flow**: It receives a SQL filter describing the desired scope. It queries grants joined to connections, agents, and members, orders the rows, converts each row into a GrantSummary object, and returns a tuple.

**Call relations**: grant_summaries and workspace_grant_summaries both call this so current-agent and whole-workspace views stay consistent.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 4 external calls (__init__, and_, select, workspace_tx).


##### `connection_summaries`  (lines 1151–1228)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the member-owned connections in the current workspace and which agents are granted each one. This gives a connection-first view instead of a grant-first view.

**Data flow**: It reads connection rows joined to owners and optionally grants and agents. It groups repeated rows from the joins by provider and account id, collects agent names, and returns ConnectionSummary objects with sorted agent lists.

**Call relations**: Connection management surfaces use this to show each connected account once, along with owner, sharing status, timestamps, and current agent access.

*Call graph*: 5 external calls (__init__, and_, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1231–1266)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. Feed registration can use these as accounts that are available without being told about a shipped or specialized agent.

**Data flow**: It queries current-workspace connections joined through grants to agents marked as the main agent, orders by provider and account id, and returns MainAgentConnection records.

**Call relations**: Feed-related code can call this to find which connected accounts the main agent may sync from, while excluding accounts granted only to other agents.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### Egress Authorization Policy
The egress proxy consults service-side policy logic to decide which destinations, credentials, and accounting records apply to sandbox network requests.

### `core/src/ufo/access/egress_control.py`

`io_transport` · `request handling`

A sandbox’s network traffic passes through a Rust proxy, but that proxy deliberately does not know customer secrets or full policy rules. This file is the control desk the proxy phones home to. Before doing anything, each route checks a bearer token, which is a shared secret in the HTTP Authorization header. Then it verifies the run or probe token that identifies the sandbox or probe making the request.

The main class, EgressControl, builds FastAPI routers for several private endpoints. One endpoint says whether a run or probe is still valid. Another turns internal rule objects into the exact JSON shape the Rust proxy understands. A metering endpoint batches network requests, model token usage, and custom counters, then writes billing records in the right workspace. A forwarding endpoint lets the proxy send a request through an approved external account without exposing that account’s credential to the proxy. A tool-bridge endpoint lets a live run ask the host to perform a bounded tool action. There is also a separate git credential route, protected by a different token, so a cache daemon can fetch only the git credential it was scoped to.

In short, this file is like a locked service window: the proxy can ask narrow questions and report usage, but the keys, policy decisions, and ledger writes stay behind the glass.

#### Function details

##### `rule_json`  (lines 55–84)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: This function converts one internal egress rule into the JSON form expected by the Rust proxy. It is important because both sides must agree on the exact field names and rule kinds, or the proxy would enforce the wrong thing or fail to read the rule.

**Data flow**: It takes a rule object, checks which kind of rule it is, and copies out only the information the proxy needs. The result is a plain dictionary with a "kind" label and rule-specific fields such as hosts, headers, sentinels, or daemon prefixes.

**Call relations**: When EgressControl._resolve has collected the allowed rules for a run or probe, it calls rule_json for each one before sending the response back to the Rust proxy.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 178–185)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the private FastAPI router for the main egress-control endpoints. It gives the Rust proxy a single group of routes for authorization, rule lookup, metering, forwarding, and tool bridge requests.

**Data flow**: It starts with the EgressControl object’s configured shared control token and route methods. It creates a router under /internal/egress, attaches the shared-token guard to every route, and registers each endpoint method. The output is a router the main server can mount.

**Call relations**: During server setup, the application asks this method for the router. FastAPI then calls EgressControl._guard before handing individual requests to _authorize, _resolve, _meter, _forward, or _tool_bridge.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 187–192)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: This builds a separate private router for the git credential callback used by the cache daemon. It intentionally uses a different token from the main egress API so that the cache daemon cannot access secrets-and-metering endpoints.

**Data flow**: It creates a router under /internal, attaches the cache-specific guard, and registers the /git-credential route. The output is a router that can be mounted by the main server.

**Call relations**: At startup, the service mounts this router separately from the main egress router. FastAPI calls EgressControl._cache_guard first, then EgressControl._git_credential only if the cache token is correct.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 194–196)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the main internal egress API has the correct shared bearer token. It stops callers who are not the trusted proxy before any policy, secret, or billing work happens.

**Data flow**: It reads the Authorization header and compares it with the expected string built from control_token. If it matches, the request continues. If it does not, it raises an HTTP 401 unauthorized error.

**Call relations**: FastAPI runs this guard automatically for every route created by EgressControl.router. Only after it passes can requests reach _authorize, _resolve, _meter, _forward, or _tool_bridge.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 198–200)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the git credential callback has the separate cache-daemon bearer token. It preserves a narrower trust boundary for the cache daemon.

**Data flow**: It reads the Authorization header and compares it with cache_control_token. A match lets the request continue. A mismatch becomes an HTTP 401 unauthorized error.

**Call relations**: FastAPI runs this guard for the router made by EgressControl.git_credential_router. If it succeeds, the request can proceed to EgressControl._git_credential.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 202–213)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: This answers the proxy’s first question: whether a run or probe token is valid right now, and which rule generation the proxy should cache against. A generation is a version number for the current policy rules.

**Data flow**: It receives the raw proxy authorization value, turns it into a run token, probe token, or nothing, and then checks liveness. Run tokens are checked against the resolver’s live-turn gate. Probe tokens are checked for expiry and then get the workspace’s current rule generation. It returns an AuthorizeResponse saying yes or no, plus a generation when available.

**Call relations**: The Rust proxy calls this before allowing a connection path to proceed. This method relies on EgressControl._principal to understand the token and on the resolver to decide whether the run is live or what policy generation applies.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (__init__, now).


##### `EgressControl._resolve`  (lines 215–217)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: This gives the proxy the actual egress rules for a verified run or probe. These rules tell the proxy which hosts, forwarded services, injected headers, or metered destinations are allowed.

**Data flow**: It receives a proxy authorization value, parses it into a principal, asks the resolver for that principal’s rules, and converts each rule into proxy-readable JSON. It returns a dictionary containing the list of serialized rules.

**Call relations**: The Rust proxy calls this when it needs the rule set. EgressControl._resolve uses EgressControl._principal to identify the caller and rule_json to translate Python rule objects into the Rust proxy’s expected format.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 219–274)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: This accepts batched usage reports from the proxy and records them for metrics and billing. It avoids writing one database record per tiny event by grouping records first.

**Data flow**: It receives a list of meter records. It groups egress counts by workspace and turn, groups token usage by workspace, turn, and model, and groups custom host/dimension counters. It emits in-process metrics for counters, then opens each workspace context and writes probe egress, run egress, and sandbox token records to the database. It returns an empty response after the writes are done.

**Call relations**: The Rust proxy calls this after observing network traffic or model-token usage. This method hands token usage through EgressControl._priced_cache_write before passing it to the accounting writer, so pricing differences are normalized before billing is recorded.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, record_egress_request, record_probe_egress_request, record_sandbox_tokens, workspace_tx, emit_metric, ws).


##### `EgressControl._priced_cache_write`  (lines 276–289)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: This adjusts token usage when a model does not have a special price for 30-minute cache writes. In that case, those tokens are billed as normal input tokens instead.

**Data flow**: It receives a model name and a Usage object. It looks up the model’s pricing. If the model supports 30-minute cache-write pricing, or there are no such tokens, it returns the usage unchanged. Otherwise it returns a copied Usage object with those cache-write tokens moved into input tokens.

**Call relations**: EgressControl._meter calls this just before recording sandbox token billing. It keeps the proxy simple: the proxy can report what it saw, while this server-side code applies the pricing rules.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._forward`  (lines 291–325)

```
async def _forward(self, body: ForwardRequest) -> ForwardResponse
```

**Purpose**: This lets the proxy forward an HTTP request through an approved connector account without giving the proxy that account’s secret. It checks both the sandbox identity and whether the requested account is allowed.

**Data flow**: It receives the proxy token, account id, HTTP method, URL, headers, and base64-encoded body. It verifies the principal, looks up the connection inside the principal’s workspace, checks that the account is shared or owned by the acting member, finds the matching CLI credential forwarder, decodes the request body, and sends the request through the connector. It returns the response status, headers, and base64-encoded body.

**Call relations**: The Rust proxy calls this when a rule says traffic should be forwarded through a connector account. This method uses EgressControl._principal for identity, the workspace database for authorization, and the provider-specific CLI forwarder to actually perform the outgoing request.

*Call graph*: calls 1 internal fn (_principal); 7 external calls (__init__, b64decode, b64encode, HTTPException, select, workspace_tx, ws).


##### `EgressControl._tool_bridge`  (lines 327–332)

```
async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse
```

**Purpose**: This lets a live sandbox run ask the host-side tool bridge to perform a limited JSON request. It only accepts run tokens, not probe tokens, because tool actions belong to an active run.

**Data flow**: It receives a proxy token and a tool bridge request. It parses the token, confirms it is a RunToken and that a bridge is configured, enters the run’s workspace context, and sends the request to the bridge. The bridge response is returned to the caller.

**Call relations**: The Rust proxy calls this for tool-bridge traffic. EgressControl._tool_bridge depends on EgressControl._principal for token checking, then hands the actual tool request to the configured ToolBridgeRequester.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (HTTPException, ws).


##### `EgressControl._git_credential`  (lines 334–348)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: This is the cache daemon’s narrow endpoint for asking for a git credential for one workspace and host. If no matching credential can be safely resolved, it tells the daemon to fetch publicly instead of using the wrong secret.

**Data flow**: It receives an optional workspace id and host. If either is missing, or credentials are not configured, it returns a public principal. Otherwise it enters that workspace and asks EgressControl._git_credential_for for the matching username and secret. It returns either username and token with a workspace-scoped principal, or no credential with a public principal.

**Call relations**: This method is reached through the separate git credential router after EgressControl._cache_guard passes. It delegates the careful slot-by-slot lookup to EgressControl._git_credential_for.

*Call graph*: calls 1 internal fn (_git_credential_for); 1 external calls (ws).


##### `EgressControl._git_credential_for`  (lines 350–376)

```
async def _git_credential_for(self, workspace_id: UUID, host: str) -> tuple[str, str] | None
```

**Purpose**: This searches the configured credential slots for the git basic-auth secret that matches a specific workspace and host. It is careful not to fall back to another slot’s identity if one slot fails.

**Data flow**: It receives a workspace id and host. It walks the resolver’s credential slots, ignores slots that are not git-basic-user injections, checks whether the slot is set, checks whether the stored credential host matches the requested host, and then reads the secret. If a slot lookup fails, it logs a warning and keeps searching. It returns a username and secret when it finds a valid match, or None when it does not.

**Call relations**: EgressControl._git_credential calls this after entering the correct workspace context. This helper uses the credential subsystem to check slot presence, host binding, and secret value, and uses warning logging when a slot cannot be resolved.

*Call graph*: called by 1 (_git_credential); 4 external calls (credential_host, slot_is_set, slot_secret, warn).


##### `EgressControl._principal`  (lines 378–388)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: This turns the raw Proxy-Authorization value into the identity it represents: either a run token, a probe token, or no valid identity. It is the common doorway for endpoints that need to know which workspace and actor a request belongs to.

**Data flow**: It receives a string. If the string is empty, it returns None. Otherwise it first tries to decode it as a run token using the configured run token codec. If that fails, it tries to decode it as a probe token using the same secret. If both attempts fail, it returns None.

**Call relations**: EgressControl._authorize, _resolve, _forward, and _tool_bridge all call this before making decisions. Those routes then use the returned principal to scope rule lookup, account forwarding, tool bridge access, or authorization status.

*Call graph*: called by 4 (_authorize, _forward, _resolve, _tool_bridge); 1 external calls (__init__).


### `core/src/ufo/access/egress_resolver.py`

`domain_logic` · `request handling`

When an agent tries to connect to the outside world, the system cannot simply let it go anywhere or attach any secret it finds. This file builds the exact rule list for one agent run or probe at the moment it is needed. Think of it like a border desk that checks a traveler’s passport, then prints a fresh list of allowed doors and special passes for that trip only.

The main class, PerAgentRules, starts with a base set of rules that apply broadly. If there is no valid principal, meaning no run or probe token identifying the request, it returns only that base. If there is a token, it enters the token’s workspace, finds which agent owns the run or conversation, and checks whether that agent is allowed internet access. From there it adds only the rules that apply: tool bridge access for normal runs, cache and preview service access when configured, stored workspace credentials for declared credential slots, and OAuth-style grant forwarding for connected accounts.

The file is careful about isolation. A token for one workspace reads only that workspace’s secrets. A token for one agent derives only that agent’s grants. Probe tokens are treated almost like runs, but the deployment’s own model API key is removed so probes do not inherit that sensitive credential by accident.

It also provides liveness checks. A run token only authorizes secret-bearing connections while its turn is still marked running in the database.

#### Function details

##### `PerAgentRules.resolve`  (lines 81–135)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the complete network rule list for one presented principal, which is either a run token, a probe token, or nothing. Someone would use it when the proxy needs to know what this particular agent execution may contact and which credentials may be injected.

**Data flow**: It receives a principal token or None. With no token, it returns the base rules only. With a run token, it looks up the turn’s agent and permissions; with a probe token, it looks up the conversation’s agent and permissions. It then combines the base rules with allowed internet rules, internal service rules, preview access, workspace credential rules, and grant-based account forwarding rules. If the principal is a probe, it removes the model-key injection before returning the final tuple of rules.

**Call relations**: This is the central entry point in the file. It calls _turn_of for normal run tokens and _conversation_of for probe tokens to learn whose authority the request carries. It then hands off to rule-building helpers such as derive_credential_rules, derive_grant_rules, and derive_cli_rules, and finally calls _without_the_model_key for probes so they do not receive the deployment model credential.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 7 external calls (__init__, __init__, derive_cli_rules, derive_credential_rules, derive_grant_rules, agent, ws).


##### `PerAgentRules._turn_of`  (lines 137–161)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: Finds the agent and internet permission attached to a specific running turn token. This gives resolve the authority information it needs before adding agent-specific rules.

**Data flow**: It receives a RunToken containing a workspace id and turn id. It opens a database transaction scoped to that workspace and reads the turn joined to its agent. If the matching turn and agent exist in that workspace, it returns an _Authority containing the agent id, the agent’s internet-access flag, and the member id from the token. If no matching row exists, it returns None.

**Call relations**: resolve calls this when the principal is a RunToken. The result tells resolve whether it may add internet-related rules and which agent context to use while deriving grants and credentials.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 163–192)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: Finds the agent and internet permission for a probe token, which identifies a conversation rather than a specific turn. This lets probes use the same agent-based policy as normal runs without pretending to be a turn.

**Data flow**: It receives a ProbeToken containing a workspace id and conversation id. It reads the conversation joined to its agent inside the matching workspace. If found, it returns an _Authority with the conversation’s agent id, the agent’s internet-access flag, and the acting member id from the token. If the conversation is missing or outside the workspace, it returns None.

**Call relations**: resolve calls this when the principal is a ProbeToken. Its answer feeds the same rule-building path used by normal runs, after which resolve removes the model-key injection for probe safety.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 194–205)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: Removes any rule that would inject the deployment’s own model key. This is used for probes, which may reach model hosts but should not automatically receive the system’s model credential.

**Data flow**: It receives a tuple of rules. It filters out rules that are credential-injection rules whose sentinel marker contains the model-key sentinel. It returns a new tuple with all other rules unchanged, including workspace credentials and account grants.

**Call relations**: resolve calls this at the end of the probe-token path. It acts as a final safety filter after all ordinary rules have been assembled.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 207–238)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: Checks whether a run token still names a turn that is currently running. It is the live authorization gate used before allowing secret-bearing egress connections.

**Data flow**: It receives a RunToken. Inside that token’s workspace, it reads the turn status and the workspace’s egress-rules generation counter from the database. If the turn does not exist or is not marked RUNNING, it returns None. If the turn is live, it returns the current generation number, which tells callers which version of the rules they are using.

**Call relations**: This function is separate from resolve because it answers a different question: not 'what are the rules?' but 'is this turn still allowed to use them right now?' The proxy-side flow can call it for each connection so a turn that has ended can no longer draw credentials.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.rules_generation`  (lines 240–251)

```
async def rules_generation(self, workspace_id: UUID) -> int
```

**Purpose**: Reads the current egress-rule generation counter for a workspace. This lets callers notice when rule-related data has changed and cached rule results may need refreshing.

**Data flow**: It receives a workspace id. It enters that workspace scope, opens a database transaction, reads the workspace’s egress_rules_generation value, and returns that integer.

**Call relations**: This supports the probe path and any flow that needs a fresh workspace-level rule version without checking a specific turn’s liveness. For run tokens, the same generation value is obtained by turn_live along with the running-status check.

*Call graph*: 3 external calls (select, workspace_tx, ws).

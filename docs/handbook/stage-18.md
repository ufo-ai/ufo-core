# Cross-cutting security, secrets, grants, and egress policy  `stage-18` (cross-cutting infrastructure)

This stage is shared behind-the-scenes safety machinery. It keeps work, secrets, and outside-service access from leaking between customers or people. The workspace helper sets a clear “current workspace” boundary, so billing, database reads, and credentials stay in the right place. Token tools create signed, tamper-evident strings for member login and artifact downloads, so the system can trust short messages without saving every token in a database. Audience checks decide who may see conversation memory or context.

Credential code encrypts secrets, verifies private handoffs, chooses provider hosts, and supports direct source connectors that use member-supplied API keys. Keyed connector settings describe which API keys owners must provide and where agents may use them. GitHub App support turns a sealed installation into a short-lived git token.

Grant code lets a member connect an OAuth account and give one agent limited, revocable use of it. Operator session rules protect admin-only tools. Composio proxying lets requests use external accounts without exposing the real token. Finally, sandbox egress rules act like a network bouncer, allowing only approved outbound calls and injecting secrets only where policy permits.

## Files in this stage

### Workspace tenancy boundary
Establishes the workspace-scoped execution context that prevents credentials, data access, and billing from crossing tenant boundaries.

### `core/src/ufo/workspace.py`

`orchestration` · `cross-cutting during turns, jobs, credential lookup, database work, and billing`

A workspace is the project or customer area a piece of work belongs to. This file creates a small “workspace scope” that is bound at the edge of a turn or background job, then reused by everything inside it. Instead of passing the workspace ID through every function, code enters `with ws(workspace_id):` once, and later calls `ws_current()` whenever it needs to know which workspace is active.

This is like putting a colored wristband on every action in a room: any secret lookup, database transaction, or billable model call can check the wristband and know which workspace it belongs to.

The file also centralizes credential lookup. A workspace can have its own stored secret, often called BYOK (“bring your own key”). If it does not, the system falls back to a platform-wide environment variable. If neither exists, the call fails clearly instead of silently using the wrong key.

Billing works the same way. Code opens a `billable_event()` block, records usage as work happens, and only writes the charges if the block finishes successfully. If an error is raised, the pending usage is dropped, so failed work is not billed. Without this file, the system would have to trust many callers to pass the right workspace, use the right key, and bill the right account every time.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that workspace code will use to find workspace-specific secrets. It is meant to run once during startup, so later credential lookups know where to ask.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module’s shared `_store` variable. After that, workspace credential lookups will either query the store first, or skip stored credentials entirely if the value was `None`.

**Call relations**: Startup code calls this before normal work begins. Later, `WorkspaceScope.credential`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` read the stored `_store` value to decide whether workspace-specific credentials can be fetched or changed.


##### `BillableEvent.usage`  (lines 49–51)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one piece of model usage to a billable event. Code uses it inside a billing block to say, “this model call should be charged if the whole operation succeeds.”

**Data flow**: It receives a model name, a usage record, and optionally pricing information. It appends those three items to the event’s private list of pending charges. Nothing is written to the database yet; it is only staged for later.

**Call relations**: Code inside `WorkspaceScope.billable_event` uses this method while work is happening. When the billing block exits successfully, `WorkspaceScope.billable_event` reads the staged usage entries and hands them to `record_workspace_usage`.


##### `WorkspaceScope.credential`  (lines 60–74)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Finds the secret value for a named credential slot in the current workspace. It protects against using the wrong customer’s key by requiring the lookup to happen through a bound `WorkspaceScope`.

**Data flow**: It takes a slot name, such as a service key name, and optionally the name of an environment variable to use as a fallback. First it asks the configured credential store for this workspace’s value. If that slot is unset, or if there is no store, it reads the fallback value from the process environment. If no value is found, it raises `CredentialSlotUnset`; otherwise it returns the secret string.

**Call relations**: Callers get a `WorkspaceScope` from `ws_current()` or from the `ws(...)` block, then call this when they need a credential. If the credential store reports that the workspace slot is unset, this function catches that specific condition and tries the environment fallback. If both paths fail, it creates a `CredentialSlotUnset` error so the caller fails loudly.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.rotate_credential`  (lines 76–81)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if the current stored value matches what the caller expected. This compare-and-swap style update helps avoid overwriting a secret that changed in the meantime.

**Data flow**: It receives a credential slot, the expected old value or marker, and the new plaintext secret. If no credential store is configured, it returns `False` because there is no workspace credential row to rotate. Otherwise it asks the store to rotate the credential for this workspace and returns the store’s success or failure result.

**Call relations**: Code that changes workspace-owned secrets calls this through the active `WorkspaceScope`. It delegates the actual secure storage update to the configured credential store, while keeping the operation tied to this workspace’s ID.


##### `WorkspaceScope.put_credential`  (lines 83–87)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores an initial credential value for this workspace. It is used when an authorized owner provides a new secret that should belong to the workspace rather than the whole platform.

**Data flow**: It receives a slot name and plaintext secret. If no credential store is configured, it raises an error because there is nowhere safe and official to store it. Otherwise it passes the workspace ID, slot, and plaintext secret to the credential store.

**Call relations**: Workspace credential setup code calls this through a `WorkspaceScope`. The function does not store the secret itself; it hands the write to the configured credential store, while ensuring the write is labeled with the current workspace.


##### `WorkspaceScope.billable_event`  (lines 90–100)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a billing block for work done by this workspace. Usage can be collected during the block, and it is charged only if the block finishes without an exception.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. The caller adds usage entries to that event. When control returns normally, the function opens a workspace database transaction and writes each usage entry to this workspace’s ledger. If there are no usage entries, it writes nothing. If the caller’s block raises an error, the code after the yield does not complete, so the staged usage is not recorded.

**Call relations**: Callers wrap billable work in this async context manager and call `BillableEvent.usage` inside it. On successful exit, this function opens a database transaction with `workspace_tx` and passes each staged charge to `record_workspace_usage`, tying the charge to the same workspace ID held by the scope.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 104–112)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Temporarily binds a workspace ID as the current workspace for a block of code. This is the main doorway that makes later calls to credentials, billing, and workspace-scoped database work refer to the right workspace without passing the ID everywhere.

**Data flow**: It receives a workspace ID. It stores that ID in the shared current-workspace context and yields a `WorkspaceScope` for the same ID. When the block ends, even if there was an error, it resets the current-workspace context back to what it was before.

**Call relations**: A turn or job boundary is expected to enter this context before doing workspace-specific work. Inside the block, `ws_current()` can read the bound value, and database code using `current_workspace` can pin work to the same workspace. This function calls the context variable’s `set` and `reset` methods and creates the `WorkspaceScope` given to the caller.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 115–121)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises a clear error instead of letting credentialed or billable work proceed without a workspace.

**Data flow**: It reads the current workspace ID from the shared context. If the value is missing, it raises `WorkspaceUnbound` with a message telling the caller to use `with ws(workspace_id):`. If a workspace ID is present, it returns a new `WorkspaceScope` for that ID.

**Call relations**: Code inside a `ws(...)` block calls this when it needs the active workspace for credentials, billing, or other workspace-scoped behavior. It reads from `current_workspace`, creates `WorkspaceScope` when successful, and creates `WorkspaceUnbound` when the required surrounding workspace block is missing.

*Call graph*: 3 external calls (__init__, __init__, get).


### Credential sources and secret storage
Defines how workspace secrets are requested, sealed, selected, and safely converted into usable credentials for GitHub Apps, keyed connectors, and direct source syncs.

### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup and git access preparation`

This file solves a sensitive identity problem: when a workspace needs to clone or fetch code from GitHub, it may need to act as an installed GitHub App rather than as an individual user. The workspace does not store the App's private key. It only stores a sealed value proving which GitHub installation belongs to that workspace. A seal is like a tamper-evident envelope: if someone types a random installation number into the credential slot, this code refuses to use it.

The main class, GitHubAppTokens, reads that sealed installation value from the credential store. If there is no installation, it returns no App token, so another normal user token can be used instead. If there is an installation, it opens the seal, asks GitHub for an installation access token, and caches that token until shortly before it expires. This avoids asking GitHub again and again during the same conversation.

The file is deliberately strict. If a stored installation value exists but cannot be opened, it raises an error instead of falling back to a user's personal token. That matters because silently switching identity could give access under the wrong authority. The helper app_tokens reads the deployment's GitHub App id and private key from environment variables, builds the signer, and returns a ready-to-use token minter.

#### Function details

##### `_segment`  (lines 45–46)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: This helper prepares one piece of a JSON Web Token, which is a signed text credential GitHub accepts from an App. It turns a small dictionary into compact JSON, encodes it in URL-safe base64, and removes padding characters as the JWT format expects.

**Data flow**: It receives a dictionary such as a token header or token body. It serializes that dictionary into compact JSON text, converts the text to bytes, base64-encodes those bytes in a URL-safe way, trims trailing equals signs, and returns the encoded bytes.

**Call relations**: GitHubAppTokens._jwt calls this twice: once for the token header and once for the token body. Those encoded pieces are then signed to prove this deployment owns the GitHub App private key.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 69–81)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This answers the yes-or-no question: does this workspace have a valid GitHub App installation bound to it? It checks for a sealed installation value and verifies that the seal can really be opened for this workspace.

**Data flow**: It receives a workspace id and a credential store. It asks the store for the configured installation slot. If the slot is unset, it returns false. If a value is present, it tries to open the sealed installation using the store's encryption helper and the workspace details. If that succeeds, it returns true; if the value cannot be opened, the error is allowed to surface.

**Call relations**: This method is used when the rest of the system needs to know whether the App credential is truly present, for example before exporting git access rules. It uses the same seal-opening check as GitHubAppTokens.secret so that both the sandbox-facing and network-facing sides make the same trust decision.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 83–107)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This returns the actual short-lived GitHub installation token for a workspace, or returns nothing when the workspace has no App installation. It is the main method callers use when they need a GitHub credential.

**Data flow**: It receives a workspace id and a credential store. It reads the sealed installation value from the store. If the slot is unset, it returns None, meaning another stored credential may answer instead. If a value is present, it opens the seal to get the installation id. It then checks whether a still-fresh token is already cached for this workspace and installation. If so, it returns that token. If not, it starts or joins an in-progress minting task, waits for it safely, and returns the newly minted token.

**Call relations**: This is the central flow that callers rely on when they need a usable secret. When a fresh token is missing, it hands off to GitHubAppTokens._mint. It uses asyncio.create_task so that concurrent callers for the same installation share one minting request, and asyncio.shield so one caller's cancellation does not cancel the shared mint for everyone.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 109–117)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: This performs one token minting operation and records the result in the cache. It also cleans up the table of in-progress minting tasks when the work finishes.

**Data flow**: It receives the cache key, made from the workspace id and installation id, plus the installation id itself. It asks GitHubAppTokens._installation_token to get a token and its expiry time. It stores that pair in the minted-token cache and returns it. Whether the request succeeds or fails, it removes its own task from the in-progress task map if it is still the current task for that key.

**Call relations**: GitHubAppTokens.secret creates this as an asynchronous task when no fresh cached token exists. This function then delegates the network exchange to GitHubAppTokens._installation_token and gives the completed token back to whichever callers were waiting.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 119–151)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: This talks to GitHub to exchange the App's signed proof of identity for an installation access token. It also checks that GitHub answered successfully and that the response contains a readable token and expiry time.

**Data flow**: It receives a GitHub installation id. It creates an HTTP client with a short timeout, builds an authorization header using GitHubAppTokens._jwt, and sends a POST request to GitHub's installation access-token endpoint. If the network call fails, if GitHub returns a non-success status, or if the response cannot be understood, it raises CredentialMintFailed. On success, it returns the token string and the expiry time as a timestamp.

**Call relations**: GitHubAppTokens._mint calls this whenever a new token is required. Before making the HTTP request, this function calls GitHubAppTokens._jwt to create the short-lived signed App credential that GitHub requires before it will issue the installation token.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 3 external calls (__init__, fromisoformat, AsyncClient).


##### `GitHubAppTokens._jwt`  (lines 153–160)

```
def _jwt(self) -> str
```

**Purpose**: This creates the signed JSON Web Token that proves this deployment owns the configured GitHub App. GitHub requires this proof before it will mint an installation access token.

**Data flow**: It reads the current time, builds a token header and body, and encodes each with _segment. The body says when the token was issued, when it expires, and which App id is making the request. It signs the header and body with the App's RSA private key using SHA-256 hashing, combines the pieces with dots, and returns the final JWT string.

**Call relations**: GitHubAppTokens._installation_token calls this just before contacting GitHub. This function depends on _segment for the JWT's encoded header and body, then adds the cryptographic signature that makes the token trustworthy to GitHub.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `app_tokens`  (lines 163–174)

```
def app_tokens(installation_slot: str) -> GitHubAppTokens
```

**Purpose**: This builds a GitHubAppTokens object from deployment configuration. It reads the GitHub App id and private key from environment variables and checks that the private key is the expected RSA kind.

**Data flow**: It receives the name of the credential slot where installation seals are stored. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the process environment. It parses the private key from PEM text, verifies that it is an RSA private key, and returns a GitHubAppTokens instance configured with the App id, key, and installation slot. If the key is the wrong kind, it raises an error.

**Call relations**: This is the setup helper used when the extension is being configured. It creates the GitHubAppTokens object that later answers bound and secret calls during credential lookup and git access preparation.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest load`

Some outside services, such as Datadog, use API keys that a workspace member already owns. A broker cannot create or host those keys for the agent. This file gives the system a safe way to use them without putting the real key inside the sandbox where code could read or leak it.

Think of it like a coat-check ticket. The sandbox receives a harmless placeholder value, called a sentinel. When a request leaves for the right service host, the egress proxy swaps that placeholder for the real secret on the wire. The agent can call the service, but it never sees the raw key.

The core idea is a table of keyed providers. Each provider row says: what service it is, which headers carry its keys, which environment variables the sandbox should see, and which API host is allowed. For services whose host depends on the customer account, such as Datadog regions, the user must choose from a fixed list. That prevents a secret from being sent to an arbitrary hostname.

The file then turns those declarations into a Manifest, which is the extension's machine-readable contract. It also builds a prompt section explaining to the agent how to request credentials and how to call these APIs safely.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that each provider declaration is valid as soon as it is created. It makes sure a provider has either one fixed host or a fixed list of selectable hosts, but not both, and that selectable-host providers include the extra details needed to ask the user and pass the chosen host into the sandbox.

**Data flow**: It reads the fields that were just placed on the KeyedProvider object. If the host setup is inconsistent, it stops immediately by raising an error. If everything is valid, it returns nothing and leaves the provider ready to be used by the rest of the file.

**Call relations**: This runs automatically when a KeyedProvider row is created for the provider table. Its job is to catch bad declarations early, before manifest builds credential slots from them.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: Returns the host rule for this provider. For a provider with one fixed API host, it returns that hostname; for a provider with regional or account-specific hosts, it builds a HostChoice, which is a safe menu of allowed hostnames.

**Data flow**: It reads the provider's host-related fields. If there is no site list, the fixed host string comes out. If there is a site list, it creates and returns a HostChoice containing the slot name, user-facing description, allowed hosts, default choice, and environment variable name.

**Call relations**: KeyedProvider.slots uses this to decide where secrets may be injected, and KeyedProvider.usage uses it to show the correct example URL. When a choice is needed, this function hands off to HostChoice.__init__ to build the structured choice object.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Turns one provider declaration into the credential slots the system can ask a workspace owner to fill. Each slot describes one secret and exactly how the proxy should replace the sandbox's placeholder with the real value on outgoing requests.

**Data flow**: It starts with the provider's target host and its list of secrets. For each secret, it creates a CredentialSlot with an InjectionTarget saying which host, header, sentinel placeholder, environment variable, and request dimension apply. If the provider uses a selectable host, it also adds one extra slot for that host choice. The result is a tuple of credential slot declarations.

**Call relations**: The manifest function gathers these slots from every provider and publishes them in the extension manifest. This function creates InjectionTarget objects for the wire-level replacement rule and CredentialSlot objects for the user-fillable secret records.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: Builds a short human-readable instruction line for one provider. The text tells the agent which slots exist and shows an example curl command using the sandbox environment variables.

**Data flow**: It reads the provider name, label, secrets, environment variable names, and host rule. It formats the needed request headers and chooses either the fixed host or the host environment variable. It returns one string that becomes part of the prompt guidance.

**Call relations**: The file uses this when building the prompt section body for all keyed providers. It connects the provider table to the instructions the agent sees, so adding a provider row also adds matching usage guidance.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest, which is the package of information the host system needs to know about this keyed-connectors extension. It publishes the credential slots and the prompt text that explains how to use them.

**Data flow**: It reads the extension name and version, collects all credential slots produced by every KeyedProvider, and uses the already-built section body as prompt text. It returns a Manifest object containing those credentials and one PromptSection.

**Call relations**: This is the file's main export for the extension system. When the extension is loaded, this function creates the Manifest by calling Manifest.__init__ and PromptSection.__init__, after the provider declarations have supplied their slots and guidance.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting`

This file is the project’s safe for “bring your own key” credentials: API tokens, provider bindings, and similar secrets that belong to a workspace. The main rule is that plain secrets should not appear in chat, logs, or the sandbox. Instead, values are encrypted before storage and only decrypted at the point where the proxy or another trusted part of the system must use them.

It also supports a sealed handoff flow. Think of a sealed request like a tamper-proof envelope: the system writes which workspace, member, and credential slot the request is for, encrypts that information, and later refuses fulfillment if the envelope was changed, expired, or meant for something else. The same idea is used for provider installations, such as an OAuth-style app binding, but with a different purpose marker so one kind of envelope cannot be reused as another.

The `CredentialStore` reads and writes encrypted credential rows in the database. `CredentialRequests` creates and checks sealed requests. `CredentialSource` lets some slots mint short-lived secrets from an external provider instead of storing a member-typed value. `HostChoice` limits account-specific hosts to a declared list, so a stored choice cannot trick the proxy into dialing an unsafe address.

#### Function details

##### `seal_credential_request`  (lines 61–62)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Turns a credential request state into an encrypted string that can safely travel through an untrusted place, such as a browser redirect or private prompt. It is used when the system needs a tamper-proof proof of what credential action was approved.

**Data flow**: It receives a Fernet encryption object and a structured request state. It turns the state into JSON, encrypts the bytes, and returns the encrypted text. The original state is not changed.

**Call relations**: Higher-level flows call this when creating sealed credential actions: member-fill requests, provider authorizations, and installation bindings. It hands back the sealed token those flows later present to opening functions for verification.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 65–91)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Checks and opens an encrypted credential request seal. It makes sure the seal was created by this deployment, has not expired when a time limit applies, is shaped correctly, and was made for the expected purpose.

**Data flow**: It receives a Fernet object, encrypted text, an expected purpose, and optionally a time limit. It decrypts the text, validates it as credential request state, checks the purpose field, and returns the trusted state. If anything is wrong, it raises a single credential-request error.

**Call relations**: Authorization checks and installation checks call this before trusting any sealed value. By centralizing the decode and purpose check here, callers do not each have to remember the same safety rules.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 110–123)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request for a member to privately fill one or more credential slots. It refuses slots that are unknown or that members are not allowed to type by hand.

**Data flow**: It receives a workspace ID, member ID, and slot names. It compares the slots against the declared and fillable slot sets, builds a request state, encrypts it, and returns the sealed string. If a slot is not allowed, nothing is sealed.

**Call relations**: This is the member-facing start of the private credential fill flow. It delegates the actual encryption to `seal_credential_request`, so later fulfillment can prove exactly which member and slots were approved.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 125–138)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed provider-authorization request for one credential slot. This is for flows where a provider callback later writes something, rather than a member typing the final secret directly.

**Data flow**: It receives a workspace ID, member ID, slot name, and provider state payload. It checks that the slot is declared and the payload is not empty, then seals those details into encrypted text. The result can be carried through the provider flow.

**Call relations**: Provider authorization code uses this to start a safe round trip. It relies on `seal_credential_request` to make the provider state tamper-proof until `CredentialRequests.open_authorization` checks it later.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 140–154)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies that a sealed provider authorization belongs to the exact workspace, member, and slot expected. If it passes, it returns the provider state that was sealed earlier.

**Data flow**: It receives sealed text plus the workspace, member, and slot that the caller expects. It opens the seal, compares every important claim, checks the slot is still declared, and returns the payload. A mismatch becomes a credential-request error.

**Call relations**: This is the matching end of `CredentialRequests.authorize`. It calls `open_credential_request` first, then adds the context-specific checks that prevent a seal for one member, workspace, or slot from being reused somewhere else.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 157–171)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Stores a provider installation ID as a sealed binding rather than as plain text. This prevents someone from typing or guessing an installation ID and making it look like their workspace owns it.

**Data flow**: It receives a Fernet object, workspace ID, slot name, and installation ID. It packages them into credential request state marked specifically as an installation binding, encrypts that state, and returns the sealed text.

**Call relations**: Provider callback or installation code uses this after a workspace has legitimately bound an external installation. It shares the sealing helper with request flows, but uses a separate purpose so request seals and installation seals cannot stand in for each other.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 174–185)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies a sealed provider installation binding. It only returns the installation ID if the binding belongs to the expected workspace and slot.

**Data flow**: It receives a Fernet object, workspace ID, slot name, and sealed binding. It opens the seal without an expiry time, checks the workspace and slot, confirms an installation ID is present, and returns that ID. Bad or mismatched input raises a credential-request error.

**Call relations**: This is the counterpart to `seal_installation`. It calls `open_credential_request` with the installation-binding purpose so provider code can safely turn a stored binding back into the provider installation ID it needs.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 191–197)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Registers the process-wide credential request authority. This lets routes that do not have normal request context, such as browser callbacks from providers, still verify sealed credential data.

**Data flow**: It receives a `CredentialRequests` object or `None` and stores it in a module-level variable. It returns nothing, but changes what later global credential-request lookups will see.

**Call relations**: Startup code is expected to call this once when the service is configured. Later functions such as `installed_credential_requests` and `authorized_slot_workspace` depend on this installed value.


##### `installed_credential_requests`  (lines 200–203)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or fails clearly if credential requests were not configured. It is a guardrail for code that cannot continue without the encryption key and slot rules.

**Data flow**: It reads the module-level installed request object. If one exists, it returns it; if not, it raises a runtime error explaining that credential authorization is unavailable.

**Call relations**: Other parts of the application can call this when they need the configured credential request machinery. It relies on `install_credential_requests` having already installed that machinery during setup.


##### `authorized_slot_workspace`  (lines 206–222)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Figures out which workspace a provider authorization callback belongs to, using only the sealed authorization value. It returns nothing if the seal is missing, invalid, for another slot, or for another provider payload.

**Data flow**: It receives sealed text, an expected slot, and an expected payload. It uses the globally installed Fernet key to open the seal, checks the slot and payload, and returns the workspace ID if everything matches. Invalid input becomes `None`, not an exception.

**Call relations**: Provider callback routes use this when the browser returns without a normal session or turn. It calls `open_credential_request` to verify the seal, then pins the result to the slot and payload expected by that provider flow.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 229–251)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Encrypts and saves a credential value for a workspace slot. It either updates the existing slot or creates it if it does not exist.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty value, encrypts the secret, opens a database transaction, updates the matching row if present, or inserts a new row otherwise. It returns nothing, but the database now holds encrypted bytes.

**Call relations**: Credential fulfillment and provider-binding code use this when a real value must be stored. It relies on the database transaction helper and SQL update/insert operations to make the change safely.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 253–265)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads and decrypts the stored credential for one workspace slot. If the slot has never been filled, it reports that specific condition.

**Data flow**: It receives a workspace ID and slot name. It queries the credential table for encrypted bytes, raises `CredentialSlotUnset` if there is no row, otherwise decrypts the bytes and returns the plaintext string.

**Call relations**: Several resolution paths call this: direct slot lookup, checking whether a slot is set, resolving account-specific hosts, and GitHub app token logic. It is the common doorway from encrypted storage back to a usable secret.

*Call graph*: called by 5 (credential_host, slot_is_set, slot_secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 267–298)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still contains an expected old value. This prevents two refreshes happening at the same time from accidentally overwriting the newer secret with an older one.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects an empty replacement, loads and decrypts the current value, compares it to the expected value, and only then writes the encrypted replacement. It returns `True` if the replacement happened and `False` otherwise.

**Call relations**: OAuth-style token refresh code can use this after getting a new token from an outside provider. The function uses a database read and conditional update so callers can tell whether their refresh won the race.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 322–327)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host-choice declaration is internally consistent. The default host must be one of the allowed hosts.

**Data flow**: After a `HostChoice` is created, it reads its default value and allowed host list. If the default is not offered by the list, it raises an error. Otherwise the object remains usable.

**Call relations**: This runs automatically when a `HostChoice` is constructed. It protects later host resolution from a broken declaration that would otherwise point to a host the declaration itself does not allow.


##### `HostChoice.resolve`  (lines 329–334)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string, if it is allowed. It treats letter case flexibly, like normal domain names, but never returns free-form user text.

**Data flow**: It receives a selected string, trims spaces, lowercases it for comparison, and searches the declared host list. It returns the canonical declared host when there is a match, or `None` when the selection is not allowed.

**Call relations**: Host resolution code uses this after reading a workspace’s selected host from credential storage. By returning only declared values, it keeps proxy rules tied to trusted configuration instead of user-entered addresses.


##### `CredentialSource.secret`  (lines 342–342)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines how a credential source can mint or fetch a secret for a workspace. A credential source is used when the deployment creates the usable secret from some provider binding instead of storing a member-typed token directly.

**Data flow**: An implementation receives a workspace ID and credential store. It may read stored binding information, talk to a provider, and return a secret string, or return `None` if there is nothing to mint.

**Call relations**: `slot_secret` calls this when a slot has a source. This protocol method is a contract: concrete provider integrations, such as app-token providers, supply the actual behavior.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 344–352)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines how a credential source can answer whether a workspace has enough binding information to mint a secret, without actually minting one. This avoids expensive or risky provider calls during ordinary “is this configured?” checks.

**Data flow**: An implementation receives a workspace ID and credential store. It checks local binding information and returns `True` or `False`, or raises if a binding exists but cannot be used safely.

**Call relations**: `slot_is_set` calls this when it needs to know whether a sourced slot would produce a credential. Provider integrations implement it alongside `secret` so export decisions and actual injection decisions stay consistent.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 355–370)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the practical question: what secret should this slot use for this workspace? It prefers a provider-minted secret when a source exists, then falls back to the stored member-provided value.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store. If there is a source, it asks the source for a minted secret and returns it if present. Otherwise it reads the stored credential; if the slot is unset, it returns `None`.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers resolve through this shared path. It calls `CredentialSource.secret` for provider-backed slots and `CredentialStore.get` for stored slots, so all consumers agree on the same answer.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 373–391)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a slot would produce a usable secret without actually producing that secret. This is useful when opening a sandbox and deciding whether to configure a client at all.

**Data flow**: It receives a slot name, optional source, workspace ID, and store. If there is a source and it says the workspace is bound, the function returns `True`. Otherwise it tries to read the stored credential; unset means `False`, successful read means `True`.

**Call relations**: Sandbox setup and similar checks use this cheaper question instead of calling `slot_secret`. It calls `CredentialSource.bound` for sourced slots and `CredentialStore.get` for stored slots, keeping the yes/no answer aligned with actual secret resolution.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 394–411)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Determines which provider host a credential is allowed to be sent to for a workspace. It supports both fixed declared hosts and account-specific choices from a safe, closed list.

**Data flow**: It receives a credential store, workspace ID, and either a plain host string or a `HostChoice`. A plain string is returned directly. For a `HostChoice`, it reads the workspace’s stored selection, uses the default if none is set, and returns the matching declared host or `None` if the stored selection is not allowed.

**Call relations**: Both the egress proxy and sandbox export logic can use this to agree on the same host. It calls `CredentialStore.get` only for host choices, then relies on `HostChoice.resolve` behavior to keep the result inside the declared safe list.

*Call graph*: calls 1 internal fn (get).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `feed sync authentication`

Some data sources need an API key that the system cannot, or should not, obtain through a normal connected-account flow. In that case, a workspace member adds the key directly. This file is the small bridge that retrieves that key when a feed sync runs.

The important safety rule is that the secret stays on the host side. The sync job is allowed to read it through CredentialAccess, which is a workspace-scoped way to fetch only the credential slots declared by the source manifest. The key is stored under the connector provider’s name. When the source is routed through the special DIRECT_ACCOUNT account handle, this class knows that the account handle is just a route marker; the real authentication value is the provider-named secret.

DirectAuthProxy has one job: take the provider name, look up the matching stored secret, and wrap it as a Credential with a bearer token. A bearer token is a secret string sent with HTTP requests to prove “I am allowed to call this API.” The file deliberately does not log, expose, or pass the raw secret to a sandbox or agent surface. Like a clerk retrieving a sealed key from a safe only when a specific delivery route needs it, it keeps the key tightly scoped to the provider HTTP call.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Fetches the API key for a given provider from the workspace credential store and returns it as a bearer credential. This is used when a source connector is authenticated by a member-added key rather than by a brokered account connection.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The account handle is not used as the secret source here; it only indicates that this direct-auth route was chosen. The function asks CredentialAccess for the secret stored under the provider name, waits for that lookup to finish, then creates and returns a Credential whose bearer field contains that secret.

**Call relations**: When a source run is routed to DirectAuthProxy, this method is the point where the run gets its provider authentication. Inside the method, it hands the fetched secret to Credential.__init__ to package it in the standard credential shape that the rest of the sync code can use for provider HTTP requests.

*Call graph*: 1 external calls (__init__).


### Signed identity and access tokens
Provides tamper-evident token machinery and applies it to artifact downloads, member login, audience checks, and operator-only sessions.

### `core/src/ufo/artifact_token.py`

`domain_logic` · `share link creation and artifact download request handling`

This file solves a simple security problem: the system needs to serve file bytes, but it must not let anyone guess a storage key or reuse an old link forever. It does this with a signed token, which is like a sealed permission slip. The token says which blob key may be downloaded, what filename should be suggested to the browser, and when the permission ends. The seal is made with a deploy secret, so if someone changes the token contents, the check will fail.

The file defines a few shared constants: artifact blob keys must start with `artifacts/`, tokens last one hour by default, and downloads live under `/artifacts/download`. It also defines `ArtifactClaims`, the clean result returned after a token has been verified.

`mint_artifact_token` turns the blob key, filename, and expiry time into JSON, then signs it. `verify_artifact_token` does the reverse: it checks the signature, reads the JSON, builds the claims, rejects keys that are not inside the artifact area, and rejects expired tokens. The path check matters because a valid signature should not accidentally grant access to unrelated stored data, such as transcripts or internal records.

#### Function details

##### `mint_artifact_token`  (lines 35–39)

```
def mint_artifact_token(secret: str, blob_key: str, filename: str, expires_at: int) -> str
```

**Purpose**: Creates a signed download token for one artifact. A caller uses it when it wants to hand someone a temporary link without exposing broad access to storage.

**Data flow**: It receives a secret, a blob key, a suggested filename, and an expiry time. If the secret is missing, it stops with an `ArtifactTokenError`, because an unsigned token would not be safe. Otherwise it packs the key, filename, and expiry into JSON bytes, signs those bytes with the secret, and returns the signed token string.

**Call relations**: This is the token-making half of the flow. It relies on `json.dumps` to make a compact payload and `ufo.token_signing.sign_token` to add the tamper-proof seal. Later, `verify_artifact_token` checks the same kind of signed payload before any artifact bytes are served.

*Call graph*: 3 external calls (__init__, dumps, sign_token).


##### `verify_artifact_token`  (lines 42–63)

```
def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether a download token is genuine, still fresh, and limited to the artifact storage area. A caller uses it before serving file bytes to decide whether the request should be allowed.

**Data flow**: It receives the token text, the shared secret, and the current time. If the secret is missing, it raises an `ArtifactTokenError`. It asks the token-signing layer to verify the signature, parses the verified bytes as JSON, and turns the fields into an `ArtifactClaims` object. It then checks that the blob key starts with `artifacts/`, does not contain a parent-directory escape like `..`, and has not expired. If all checks pass, it returns the claims; otherwise it raises an `ArtifactTokenError` and no download should happen.

**Call relations**: This is the token-checking half of the flow, typically used by the artifact download route before reading from storage. It hands the raw token to `ufo.token_signing.verify_token`, parses the result with `json.loads`, uses `PurePosixPath` to inspect the storage-style path safely, compares the expiry with `datetime.timestamp`, and returns `ArtifactClaims` only after every gate has passed.

*Call graph*: 6 external calls (__init__, __init__, timestamp, loads, PurePosixPath, verify_token).


### `core/src/ufo/audience.py`

`domain_logic` · `cross-cutting`

A conversation can carry information forward into later turns, so the system needs a precise label for who is allowed to see that information. This file creates those labels and checks them whenever they are read or changed. Think of an audience like a stamp on a folder: “shared,” “member:...,” “room:surface:room,” or “foreign:surface:room.” The stamp decides which saved context may be opened.

The file uses a lightweight string type called Audience, so audience values are still strings but are treated as a special kind of string in the code. It provides builders for common audience kinds: a shared conversation, a specific member conversation, an internal room, and a room that includes an outside organization. It also validates audience text through parse_audience, rejecting malformed labels such as missing pieces or unexpected colons.

The most important privacy rule is in audience_subjects. Normal audiences may read both shared workspace information and their own audience-specific information. A foreign room is stricter: it may read only its own foreign-room subject, never the workspace-shared subject. This prevents internal shared context from being recalled into a channel where outsiders are present.

Finally, narrow_audience allows a conversation to become more specific, while blocking unsafe changes from one unrelated audience to another.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Creates the audience label for a conversation that is either shared by everyone or tied to one specific member. This is used when the system needs a standard, safe spelling for those two audience types.

**Data flow**: It receives either a member UUID, which is a unique identifier, or None. If it gets None, it returns the shared audience label. If it gets a UUID, it turns it into a member-specific label using the member prefix.

**Call relations**: parse_audience calls this when it checks a member audience string. Instead of trusting the input text, parse_audience rebuilds the expected label with this function and compares the two.

*Call graph*: called by 1 (parse_audience).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates the audience label for an internal room on a named surface, such as a chat platform or workspace area. It gives room audiences one consistent format.

**Data flow**: It receives a surface name and a room name. It passes them, along with the internal room prefix, to the shared helper that checks the pieces and builds the final audience string.

**Call relations**: parse_audience uses this when validating an audience that claims to be an internal room. This function hands the actual checking and string building to _room_audience.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates the audience label for a room that includes an outside or foreign organization. This separate label matters because foreign rooms have stricter privacy rules later.

**Data flow**: It receives a surface name and a room name. It passes them, along with the foreign room prefix, to the shared helper, which validates the parts and returns the final audience.

**Call relations**: parse_audience uses this to verify foreign room labels. Like room_audience, it relies on _room_audience so internal and foreign room labels follow the same basic shape.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Builds a room-style audience label after making sure the surface and room names are safe to put into that label. It prevents ambiguous labels by forbidding empty values and extra colons.

**Data flow**: It receives a prefix, a surface name, and a room name. It checks that the surface and room are not empty and do not contain colons, because colons are used as separators. If the input is valid, it returns a combined audience string; if not, it raises an error.

**Call relations**: room_audience and foreign_room_audience both call this helper. It is the shared gatekeeper that keeps both room audience formats consistent.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks whether a text value is a valid audience label and returns it as an Audience. This is the main safety checkpoint before other code trusts an audience value.

**Data flow**: It receives a string. It first accepts the exact shared audience. Otherwise it splits the string around colons, checks whether it is a member, room, or foreign-room audience, and rebuilds the expected value using the proper constructor. If the text does not match a valid format, it raises an error; if it is valid, it returns the audience.

**Call relations**: audience_member, audience_subjects, and narrow_audience all call this before making decisions. It calls conversation_audience, room_audience, and foreign_room_audience to compare incoming text with the system’s canonical audience formats.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `audience_member`  (lines 58–62)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Extracts the member ID from a member-specific audience. If the audience is not for one member, it reports that by returning None.

**Data flow**: It receives an Audience value and first validates it with parse_audience. If the validated audience does not start with the member prefix, it returns None. If it does, it removes the prefix and turns the remaining text back into a UUID.

**Call relations**: This function depends on parse_audience so it never extracts an ID from malformed text. It does not call other project functions after validation, but it uses the UUID parser to turn the stored string back into a unique member identifier.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 65–72)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Decides which stored information subjects a conversation audience is allowed to read. This is where the key privacy rule for foreign rooms is enforced.

**Data flow**: It receives an Audience and validates it with parse_audience. If the audience is a foreign room, it returns only that foreign room as readable. For all other valid audiences, it returns both the shared subject and the audience’s own subject.

**Call relations**: This function is called after an audience is known or supplied and the system needs to know what memory or context can be recalled. It relies on parse_audience to reject bad labels before applying the access rule.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 75–90)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Safely combines a current audience with a newly requested audience. It allows the conversation to stay the same or become more specific, but blocks switches to unrelated audiences.

**Data flow**: It receives the current audience and a requested audience. It validates both, then applies rules: requesting shared keeps the current audience; starting from shared can narrow to the requested audience; matching internal and foreign room keys choose the stricter foreign version when needed. If the request would move to an unrelated audience, it raises an error.

**Call relations**: This function calls parse_audience on both inputs before comparing them. It is the policy step used when a conversation’s audience might change, making sure the change is a narrowing of access rather than an unsafe jump.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/bearer.py`

`domain_logic` · `request handling and token creation`

This file is the shared rulebook for UFO bearer tokens. A bearer token is a string a client presents as proof of identity, like a wristband at an event. The important risk is that users must not be able to edit the wristband to claim a different workspace or email. To prevent that, the file signs each token with a secret key using HMAC, which is a tamper-evident digital stamp made from a shared secret.

When a token is minted, the file builds a small JSON payload containing the workspace id, the member email, and an expiry time. It encodes that payload in URL-safe base64, then adds a SHA-256 HMAC signature. The final token is two parts separated by a dot: the encoded payload and the signature.

When a token is checked, the file reads the signing secret from the UFO_TOKEN_SECRET environment variable, recomputes the expected signature, compares it safely, decodes the payload, checks that the fields have the right shape, and rejects expired tokens. Higher-level helpers then either verify that the token belongs to one specific workspace or extract the workspace claim for shared services that serve many workspaces.

#### Function details

##### `mint_token`  (lines 28–45)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed token that says a particular email belongs to a particular workspace until a chosen expiry time. This is used by token issuers so every part of the system creates tokens in the same format.

**Data flow**: It takes a secret key, workspace id, email address, time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates an expiry timestamp, turns the claims into compact JSON, encodes that JSON as URL-safe text, signs the encoded text with HMAC-SHA256, and returns one token string containing the body and signature.

**Call relations**: This is the issuing half of the token story. Other services or commands call it when they need to give someone a bearer token. The checking half is `verified_claims`, which expects exactly the same signed shape.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 48–71)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a token is genuine and still valid, then returns the workspace and email it proves. If anything looks wrong, it returns nothing instead of trusting the token.

**Data flow**: It takes a token string and optionally a current timestamp for testing or controlled checks. It reads the secret from `_secret`, splits the token into payload and signature, recreates the expected signature, compares it safely, decodes the payload through `_b64url_decode`, reads the JSON fields, checks their types, checks the expiry time, and returns `(workspace, email)` only if all checks pass.

**Call relations**: This is the central verification step used by both `verify_token` and `workspace_claim`. Those functions ask it first, because neither the workspace nor email should be trusted until the signature and expiry have been proven.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 74–85)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Confirms that a token is valid for one specific workspace and returns the member email. This is useful for a service that is pinned to a single workspace and must reject tokens from any other workspace.

**Data flow**: It receives a token, the expected workspace UUID, and optionally a current timestamp. It asks `verified_claims` to prove the token first; if that fails, it returns `None`. If the signed workspace does not match the expected workspace, it also returns `None`. Otherwise it returns the lowercased email address.

**Call relations**: This function builds on `verified_claims` by adding the tenant check: not just 'is this token real?' but 'is this real token for this workspace?' It does not decode or sign anything itself; it delegates the trust decision first, then applies the workspace match.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 88–99)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace UUID from a valid token. This is for shared services that handle many workspaces and need to decide which workspace a request belongs to from the signed token itself.

**Data flow**: It receives a token and optionally a current timestamp. It asks `verified_claims` to confirm the token and read the claims. If verification succeeds, it tries to turn the workspace string into a UUID object. It returns that UUID when valid, or `None` if the token is bad, expired, or contains a workspace value that is not a UUID.

**Call relations**: Like `verify_token`, this function relies on `verified_claims` before trusting the payload. Instead of comparing the workspace to a preconfigured value, it hands the verified workspace onward as the request scope.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 102–106)

```
def _secret() -> str
```

**Purpose**: Reads the signing secret used to verify bearer tokens. Without this secret, the system cannot know whether a token was truly issued by a trusted party.

**Data flow**: It reads the `UFO_TOKEN_SECRET` environment variable. If the value is present, it returns it. If it is missing or empty, it raises an error that clearly says token verification cannot run without the secret.

**Call relations**: `verified_claims` calls this before checking a token signature. This keeps secret loading in one place, so callers only pass tokens around and do not need to handle the signing key themselves.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 109–110)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the token payload from URL-safe base64 text back into bytes. It also restores missing padding, because the token format strips padding characters to keep the token shorter and cleaner.

**Data flow**: It takes the encoded payload string, adds the right number of `=` padding characters, decodes it with URL-safe base64 rules, and returns the original bytes that can then be parsed as JSON.

**Call relations**: `verified_claims` uses this after the signature has been accepted, so it can read the signed JSON payload. This helper keeps the slightly fussy base64 padding detail out of the main verification flow.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/ext/operator.py`

`domain_logic` · `operator request handling`

This file solves a security and reuse problem for internal operator web pages. Several operator-only tools need the same idea of “who is this operator, and which workspace are they looking at?” Rather than each tool inventing its own login flow, this file gives them one shared session system.

The most important rule is that the bearer token, meaning the secret credential proving access, must never come from a URL query parameter. URLs often end up in browser history, server logs, analytics tools, or chat previews. Instead, the token can come from an Authorization header, from a secure session cookie, or from the body of the one form POST that opens a session.

Once a token is found, the file asks the bearer-token verifier to check it. This module does not keep the signing secret itself; it delegates that check. After verification, it allows access only if the email address belongs to the special operator email domain. That domain check is the gate that separates internal operator access from normal user access.

For workspace selection, the request may include `?ws=` after the operator is verified. If absent, the workspace inside the token is used. If present, it may be a raw workspace UUID, or a customer domain that gets converted into a stable UUID. Finally, `bind_operator_session` stores the posted token in one shared cookie so the operator logs in once and can move across all operator tools.

#### Function details

##### `operator_bearer`  (lines 24–37)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: Finds the operator bearer token for a web request without ever reading it from the URL. It checks the safer places in order: Authorization header, shared operator cookie, and finally a POST form field used only when opening a session.

**Data flow**: It receives a web request. First it reads the Authorization header and returns the token if it is a proper `Bearer ...` header. If not, it checks the shared operator cookie. If there is still no token and the request is a POST, it reads the submitted form and looks for the `token` field. It returns the cleaned token text, or an empty string if none is found.

**Call relations**: This is the first step used by `resolve_operator_workspace` when an operator-only page needs to know who is making the request. It calls the request's form-reading method only for POST requests, so normal page loads do not unnecessarily parse a form body.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 40–64)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Decides which workspace an operator request is allowed to view. It verifies the token, confirms the requester belongs to the operator email domain, and then chooses either the token's own workspace or the workspace named by `?ws=`.

**Data flow**: It receives the web request and the surface authentication object. It asks `operator_bearer` for a token. If there is no token, or the token fails verification, it returns `None`, meaning the request should be rejected. If the token is valid, it reads the workspace claim and email address from it, checks that the email domain is the trusted operator domain, and then chooses a workspace. Without `?ws=`, it converts the workspace claim into a UUID. With `?ws=`, it accepts either a UUID directly or turns a domain name into a stable UUID using DNS-based UUID generation.

**Call relations**: This function is the main authorization bridge for operator-only surfaces. It builds on `operator_bearer` to get the credential, then hands the token to `verified_claims` for checking, uses `_email_domain` to enforce the operator-domain gate, and uses UUID conversion helpers to produce the final workspace identity the rest of the surface can use.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, _email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 67–79)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens an operator web session by taking a token submitted in a form, saving it as the shared operator cookie, and redirecting the browser back to the page. This lets the operator authenticate once and then browse the operator tools without putting the token in URLs.

**Data flow**: It receives the current surface context and web request. It reads the submitted form and looks for a non-empty `token` field. If the field is missing or invalid, it returns a JSON error with HTTP status 400. If the token is present, it creates a redirect response pointing back to the current URL, attaches the token as the shared operator session cookie, and returns that response to the browser.

**Call relations**: This function is used at the moment an operator session is created. It relies on the surrounding surface flow to have already verified the same form token before this handler runs. It calls the response helpers to either report a bad form submission or redirect after setting the cookie, so later requests can be authorized by `operator_bearer` through that cookie.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file is a small security helper. It turns a byte payload into an opaque token, meaning callers do not have to care what the token looks like inside. The token has two parts separated by a dot: the payload encoded as URL-safe text, and a signature. The signature is made with HMAC, which is a standard way to prove that someone who knows a shared secret approved a message. Think of it like sealing an envelope with a wax stamp: people can carry the envelope around, but if they change the contents, the stamp will no longer match.

The file also defines SignedTokenError, the error used when a token is missing pieces, has the wrong signature, or contains payload text that cannot be decoded back into bytes.

The important safety detail is that verification checks the signature before returning the payload. It also uses a constant-time comparison function, hmac.compare_digest, which avoids leaking tiny timing clues about the expected signature. Without this file, other parts of the system would need to invent their own token format and signature checks, which is easy to get subtly wrong.

#### Function details

##### `sign_token`  (lines 12–16)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Creates a signed token from a secret key and a byte payload. Someone would use it when they need to give a client or another service a compact text value that can later be checked for tampering.

**Data flow**: It takes a secret as bytes and a payload as bytes. First it turns the payload into URL-safe base64 text, which is text that can safely appear in links or headers. Then it calculates an HMAC-SHA256 signature over that text using the secret. It returns one string containing the encoded payload, a dot, and the encoded signature.

**Call relations**: This is the token maker in the pair. It relies on base64 encoding to make binary data safe as text, and on hmac.new to produce the signature. Later, verify_token expects the exact format produced here and checks that the signature still matches.

*Call graph*: 2 external calls (urlsafe_b64encode, new).


##### `verify_token`  (lines 19–30)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks whether a signed token is well formed and was signed with the expected secret, then returns the original payload bytes. Someone would use it before trusting any data that came back from a user, browser, or outside system.

**Data flow**: It takes a token string and the secret bytes that should have been used to sign it. It splits the token into the encoded payload and signature. If either part is missing, it raises SignedTokenError. It recalculates the expected signature from the payload text and compares it safely with the provided signature. If they differ, it raises SignedTokenError. If the signature is valid, it decodes the payload text back into bytes and returns those bytes; if decoding fails, it raises SignedTokenError.

**Call relations**: This is the token checker that completes the flow started by sign_token. It uses the same base64 and HMAC recipe so it can reproduce the expected signature. When anything looks wrong, it raises SignedTokenError instead of returning untrusted data.

*Call graph*: 5 external calls (__init__, b64decode, urlsafe_b64encode, compare_digest, new).


### Connected-account grants
Manages controlled OAuth-style account grants and isolated proxy access so agents can use external services without receiving raw tokens.

### `core/src/ufo/grants.py`

`domain_logic` · `request handling and cross-cutting grant lookup`

This file is the project’s “connected accounts and grants” center. It solves a common safety problem: an agent may need to use a member’s outside account, but the system should not hand the agent the real password or token. Instead, a member goes through an OAuth flow, which is the familiar “authorize this app” web redirect. The broker keeps the actual token, and this file records which agent may use which connected account.

The flow has two halves, like leaving a sealed claim ticket at a coat check. `ConnectFlow.authorize` creates a provider login link and seals important facts into the OAuth `state` value: workspace, agent, provider, member, conversation, and whether the grant is shared. `ConnectFlow.complete` opens that sealed state after the browser returns, exchanges the provider’s code for a stable account id, and asks `GrantStore` to save the connection and the agent’s grant.

`GrantStore` is the database-facing part. It creates or reuses a member-owned connection, prevents a connected account from being silently taken over by another member, lists active grants, revokes grants, changes sharing, and disconnects accounts. The file also exposes summary functions for audit/operator views and a process-wide installed connect flow used by tools and callback routes. Without this file, agents could not safely receive OAuth-backed access, and the system would either leak secrets or have no durable record of who granted what.

#### Function details

##### `grant_sentinel`  (lines 34–38)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a fake-looking credential value that stands in for a real connected account token. The agent can carry this marker, while the server-side proxy recognizes it and routes the request through the broker without exposing the secret.

**Data flow**: It takes a connected account id as text, prefixes it with a fixed marker string, and returns the combined sentinel value. It does not read or change stored data.

**Call relations**: This small helper is the handshake between the sandbox environment and the egress proxy: both can independently compute the same marker from the same account id, so no extra registration step is needed.


##### `OAuthProvider.provider`  (lines 84–84)

```
def provider(self) -> str
```

**Purpose**: Names the OAuth provider represented by a connector descriptor. Other code uses this stable name when storing and checking connected accounts.

**Data flow**: A concrete provider implementation supplies the value. The property has no body here because this is a protocol, meaning it describes what provider objects must offer.

**Call relations**: Connector extensions implement this property, and the connect flow uses the resulting provider name when recording a completed grant.


##### `OAuthProvider.host`  (lines 87–87)

```
def host(self) -> str
```

**Purpose**: Gives the provider host that the grant should allow through the outbound proxy. In plain terms, it tells the system which outside web address this connected account is meant for.

**Data flow**: A concrete provider implementation returns a host string. This protocol only states that the value must exist.

**Call relations**: The connect flow reads this from the provider descriptor after OAuth succeeds and passes it into grant recording, where it becomes part of the saved connection.


##### `OAuthProvider.authorize_url`  (lines 89–89)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the web link a member opens to approve account access with the provider. It includes sealed state so the return trip can be checked later.

**Data flow**: It receives the sealed state value and the callback address, then returns a URL for the member’s browser. The actual URL-building is supplied by each connector implementation.

**Call relations**: ConnectFlow.authorize calls this after it has prepared the sealed state, so the member is sent to the right provider consent page.


##### `OAuthProvider.exchange`  (lines 91–93)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the short-lived OAuth callback code into a stable broker-side connected account id. This is where the provider confirms which account was authorized.

**Data flow**: It receives the provider code, callback address, workspace id, and original state. A concrete provider talks to the broker or provider service and returns an OAuthAccount containing the account id; the secret token remains server-side.

**Call relations**: ConnectFlow.complete calls this after validating the sealed state, then uses the returned account id to record the connection and grant.


##### `OAuthProviderResolver.claims`  (lines 104–104)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open-ended connector namespace can serve a provider name. This catches typos or unavailable providers before making a dead authorization link.

**Data flow**: It receives a provider slug, may consult an outside catalog, and returns true or false. It does not itself create a provider descriptor.

**Call relations**: ConnectFlow.validate_provider uses this when a provider is not in the fixed provider map but a resolver is installed.


##### `OAuthProviderResolver.descriptor`  (lines 106–106)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Creates an OAuthProvider descriptor for a provider name that belongs to an open connector namespace. It lets one broker extension serve many provider slugs.

**Data flow**: It receives the provider slug and returns an object that knows how to authorize and exchange for that provider. The protocol leaves the actual construction to the resolver implementation.

**Call relations**: ConnectFlow._provider falls back to this when the provider is not explicitly registered but a resolver exists.


##### `GrantStore.workspace_id`  (lines 181–182)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently in scope. It keeps database operations tied to the workspace that the running request or task is acting inside.

**Data flow**: It reads the current workspace context through ws_current and returns its workspace_id. It does not modify anything.

**Call relations**: GrantStore methods use this property when selecting, inserting, updating, or deleting rows so they do not cross workspace boundaries.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 185–186)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent id currently in scope. It lets grant operations know which agent is being granted or queried.

**Data flow**: It reads the current agent context through agent_current and returns its agent_id. It does not change stored data.

**Call relations**: GrantStore.record and grant lookup methods use this value to attach or find grants for the bound agent.

*Call graph*: 1 external calls (agent_current).


##### `GrantStore.record`  (lines 188–273)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> None
```

**Purpose**: Saves a completed OAuth connection and grants the current agent access to it. It also protects ownership: if the same provider account is already owned by another member in the workspace, the record is refused.

**Data flow**: It receives provider, account id, host, grantor member id, conversation id, and sharing flag. Inside a workspace database transaction, it inserts the connection if needed, locks and verifies the existing owner, updates the host timestamp, then inserts or updates the agent’s grant edge. It returns nothing, but the database now has the durable connection and grant.

**Call relations**: ConnectFlow.complete calls this after the provider exchange succeeds. It uses workspace_tx for one safe transaction, uuid4 for new row ids, and raises ConnectionOwnedByAnotherMember when saving would steal another member’s account.

*Call graph*: 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 275–312)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the connected accounts currently available to the bound agent. This is the agent’s usable view of all grants it has been given.

**Data flow**: It reads the current workspace and agent, joins grant rows to their connection rows, and turns each database row into a Grant object. The output is a tuple of grants; the database is not changed.

**Call relations**: The queue code’s _grant_cli_env calls this when preparing the command-line environment for an agent, so granted connections can be represented safely.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 314–326)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent after checking that the acting member is allowed to do it. Revoking the edge stops that agent from using the connection but does not necessarily delete the connection itself.

**Data flow**: It receives a grant id and actor member id. It opens a transaction, asks _grant_for_actor to verify the grant and permissions, deletes the grant row if allowed, and returns true if a row was removed or false if no matching grant was available.

**Call relations**: User-facing or operator actions can call this to revoke access. It delegates the permission check to _grant_for_actor, then performs the delete inside workspace_tx.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.set_shared`  (lines 328–353)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether a grant is marked as shared. It rechecks permissions first, because changing sharing affects who may rely on the grant.

**Data flow**: It receives a grant id, the desired shared value, and the acting member id. It verifies the grant through _grant_for_actor, updates the shared flag and timestamp if allowed, and returns whether an update happened.

**Call relations**: This is used when a member or permitted admin changes the sharing state of an agent grant. It uses _grant_for_actor for the safety check and workspace_tx for the database update.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (update, workspace_tx).


##### `GrantStore.disconnect`  (lines 355–405)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a connected account from the workspace, after checking that the acting member may do so. It also detaches related feed sources and marks their pages as tombstoned, meaning “kept as a removed record, not active content.”

**Data flow**: It receives a connection id and actor member id. It verifies permission with _connection_for_actor, finds sources tied to the connection, clears and marks those sources removed, tombstones their pages, then deletes the connection row. It returns false if the connection was not found for that actor, otherwise true.

**Call relations**: This is the stronger cleanup path compared with revoking one grant. It calls _connection_for_actor for authorization, then uses selects, updates, and delete statements inside one workspace transaction.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 407–444)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific connection. The owner can act directly; an admin may act when admin access is allowed.

**Data flow**: It receives an open database connection, a connection id, an actor member id, and a flag saying whether admin override is allowed. It locks and reads the connection, compares the owner, optionally checks the member’s admin flag, and returns the connection id if allowed. It returns None when the connection is absent and raises ConnectionPermissionDenied when the actor is not allowed.

**Call relations**: GrantStore.disconnect calls this before deleting a connection, and _grant_for_actor calls it before allowing changes to a grant tied to that connection.

*Call graph*: called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._grant_for_actor`  (lines 446–484)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may change a specific grant for the current agent. It makes sure the grant exists, belongs to the bound agent, and points to a connection the actor may control.

**Data flow**: It receives an open database connection, grant id, actor member id, and admin policy. It reads the grant’s connection id, asks _connection_for_actor to verify ownership or admin rights, then locks and returns the grant id if everything still matches. It returns None if the grant is missing.

**Call relations**: GrantStore.revoke and GrantStore.set_shared use this helper before changing grant rows, so both actions share the same permission logic.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 502–522)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Creates the OAuth authorization URL that a member should open in a browser. It seals all the important context into the state value so the callback can later prove what request it belongs to.

**Data flow**: It receives workspace id, agent id, provider name, granting member id, conversation id, and sharing choice. It finds the provider descriptor, builds a ConnectState object, encrypts it with Fernet, and returns the provider’s authorization URL.

**Call relations**: ConnectHandoff.authorize uses this when a terminal connect request needs its first URL. It relies on _provider to find the correct connector descriptor.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 524–529)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a requested provider is actually available before starting a connect request. This prevents the system from offering a login link for a provider it cannot complete.

**Data flow**: It receives a provider name. It accepts the name if it is in the installed provider map or if the resolver claims it; otherwise it raises UnknownProvider. It returns nothing when the provider is valid.

**Call relations**: This is used early in connect-request handling, before the private handoff URL is made, while knows_provider is the cheaper later check.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 531–536)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether the connect flow still has machinery for a provider. It is a lightweight check, not a full outside catalog validation.

**Data flow**: It receives a provider name and returns true if it is explicitly installed or if a resolver is present. It does not call outside services or change data.

**Call relations**: ConnectHandoff.authorize uses this while holding the turn row lock, so it can reject stale connect requests if provider support has disappeared.


##### `ConnectFlow.bridge_workspace`  (lines 538–544)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to run as. This stops a callback-like request from pretending to belong to another workspace or provider.

**Data flow**: It receives the sealed state, provider name, and callback URL from the browser request. It opens and validates the state, compares provider and callback against expected values, checks that the provider can be resolved, and returns the workspace id. If anything does not match, it raises ConnectStateInvalid or UnknownProvider.

**Call relations**: connect_bridge_workspace calls this through the installed connect flow and turns failures into None, so the route can reject bad bridge requests cleanly.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 546–561)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth callback. It proves the state is valid, exchanges the provider code for the connected account id, and records the grant for the intended agent.

**Data flow**: It receives sealed state and an OAuth code. It decrypts the state, finds the provider descriptor, enters the sealed workspace and agent contexts, exchanges the code, records the connection and grant in GrantStore, then returns a GrantRecorded summary.

**Call relations**: The OAuth callback path calls this after the provider redirects back. It uses _open for state validation, _provider for connector lookup, ws and agent context wrappers so GrantStore writes into the right scope, and then returns the recorded provider/account/agent facts.

*Call graph*: calls 2 internal fn (_open, _provider); 3 external calls (__init__, agent, ws).


##### `ConnectFlow._provider`  (lines 563–569)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. It supports both explicitly registered providers and an optional resolver for open-ended provider namespaces.

**Data flow**: It receives a provider name. It first looks in the providers mapping, then asks the resolver to build a descriptor if one exists, and raises UnknownProvider if neither path works.

**Call relations**: ConnectFlow.authorize, bridge_workspace, and complete all call this so they use the same provider lookup rule.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 571–576)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. This is the main guard against tampered or expired browser redirects.

**Data flow**: It receives the state string, decrypts it with Fernet using a time limit of ten minutes, and parses it into a ConnectState object. If decryption fails or the state is too old, it raises ConnectStateInvalid.

**Call relations**: ConnectFlow.bridge_workspace and complete call this before trusting any information from the browser return leg.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 585–667)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates or reuses the private OAuth URL for a terminal connect request. It makes sure the request still exists, belongs to the speaking member, has not expired, and still names an available provider.

**Data flow**: It receives workspace id, turn id, and member id. It locks the turn row, reads the terminal connect request, validates ownership and age, returns a previously saved authorization URL if it is still fresh, or asks the connect flow to create one and saves it back to the turn. The output is the URL to open.

**Call relations**: This function sits between a UI surface and ConnectFlow.authorize. It uses workspace_tx, TerminalFrame validation, current time checks, and a database update so repeated clicks reuse the same short-lived URL instead of creating competing handoffs.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 673–681)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide ConnectFlow object, or clears it when grants are unavailable. This avoids passing the same fixed connect setup through every tool call and callback handler.

**Data flow**: It receives a ConnectFlow or None and stores it in the module-level _installed_flow variable. It returns nothing and only changes that in-process setting.

**Call relations**: Startup code calls this before serving requests, and tests can call it to install a stub. installed_connect_flow later reads the value.


##### `installed_connect_flow`  (lines 684–687)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed ConnectFlow or fails loudly if the deployment has no credential key and therefore cannot use grants. This gives callers one clear way to access the singleton flow.

**Data flow**: It reads the module-level _installed_flow value. If present, it returns it; if absent, it raises ConnectUnavailable.

**Call relations**: connect_bridge_workspace calls this before verifying bridge requests. Other connect-related surfaces can use the same accessor instead of touching the global directly.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 690–699)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming browser bridge request is valid and, if so, returns the workspace id it belongs to. Invalid or unavailable connect setup becomes a simple None result.

**Data flow**: It reads state, provider, and callback query parameters from the Starlette Request. It asks the installed connect flow to verify them and returns the workspace id on success; if state, provider, or setup checks fail, it returns None.

**Call relations**: A web route can call this before running bridge logic. It uses installed_connect_flow and catches ConnectStateInvalid, ConnectUnavailable, and UnknownProvider so bad requests are rejected without exposing internal errors.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `grant_summaries`  (lines 702–709)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style rows for the current agent’s connector grants. It is useful when showing what outside accounts this agent can use.

**Data flow**: It reads the current workspace and agent contexts, builds a database filter for that scope, and passes the filter to _grant_summaries. The output is a tuple of GrantSummary objects.

**Call relations**: This is the current-agent wrapper around the shared _grant_summaries query helper.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, agent_current, ws_current).


##### `workspace_grant_summaries`  (lines 712–715)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style grant rows for an entire workspace. This is broader than grant_summaries and is intended for an operator or workspace-level view.

**Data flow**: It receives a workspace id, enters that workspace context, and calls _grant_summaries with a workspace-wide filter. The output is a tuple of GrantSummary objects.

**Call relations**: This function reuses the same summary-building helper as grant_summaries but changes the scope from one agent to the whole workspace.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 718–758)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into readable audit summaries. It joins grants to connections and agents so each row has both account identity and agent name.

**Data flow**: It receives a SQL filter describing the desired scope. It opens a workspace transaction, selects grant, connection, and agent fields ordered by provider and agent name, then converts each row into a GrantSummary. It does not change the database.

**Call relations**: grant_summaries and workspace_grant_summaries both call this helper, which keeps the summary query consistent across agent-level and workspace-level views.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 3 external calls (__init__, select, workspace_tx).


##### `connection_summaries`  (lines 761–823)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the workspace’s connected accounts and the agents currently granted each one. This gives a connection-centered view rather than an agent-grant-centered view.

**Data flow**: It reads the current workspace, selects connections with optional grant and agent joins, groups rows by provider and account id, gathers agent names, and returns ConnectionSummary objects with sorted agent lists. The database is only read.

**Call relations**: Operator or settings surfaces can call this to show connected accounts independently of the currently bound agent. It uses workspace_tx for the query and ws_current to stay inside the active workspace.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Some connectors need to talk to outside providers, such as an API owned by another company. But with Composio, the connector is not allowed to hold the provider’s secret token directly. Instead, Composio keeps that credential and injects it on the server side. This file is the adapter that makes that arrangement feel like ordinary HTTP to the rest of the code.

The main piece, `ComposioProxyTransport`, is an HTTP transport, meaning it is the part of an HTTP client that actually sends requests. When a connector tries to call a provider URL, this transport repackages the method, URL, query values, safe headers, and body into a request to Composio’s `/tools/execute/proxy` endpoint. Composio performs the real provider call using the connected account’s credential. The transport then rebuilds the provider’s status code, headers, and body so the connector can keep working normally, including pagination based on response headers.

The file also protects the shared proxy process. It can cap how much response data is buffered, and it turns large binary provider results into a redirect to Composio’s stored file URL instead of pulling all bytes through this process. `ComposioRequestForwarder` uses the same machinery for one-off forwarded CLI requests, with a hard timeout so a slow or stuck broker cannot tie up the service forever.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 63–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request rewrite step. It takes a normal provider HTTP request and sends it to Composio’s proxy endpoint instead, so Composio can add the hidden provider credential.

**Data flow**: It starts with an incoming HTTP request: method, URL, query values, headers, timeout, and optional body. It reads the body, builds a JSON payload containing the connected account id and the request details, skips headers that should not be forwarded, and sends a new POST request to Composio. It then reads Composio’s response with size protection. If Composio itself reports an error, that error is returned directly. Otherwise, the Composio payload is converted back into a provider-style HTTP response.

**Call relations**: This is the entry point used by the HTTP client transport. During its work it asks `_read_bounded` to safely collect the proxy response body, then hands the decoded successful payload to `_provider_response` so callers receive a normal response instead of Composio’s wrapper format.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the body of Composio’s response while optionally enforcing a maximum size. It exists to stop a shared proxy process from accidentally buffering a huge response in memory.

**Data flow**: It receives an HTTP response from Composio. If no size cap is set, it simply reads the whole body. If a cap is set, it reads the body in chunks, adding each chunk to a buffer and checking the total size. If the response grows too large, it closes the response and raises a Composio error. Otherwise, it returns the collected bytes.

**Call relations**: `handle_async_request` calls this immediately after sending the proxy request. Its result becomes either the error body returned to the caller or the JSON bytes that `_provider_response` later rebuilds into the provider response.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio’s wrapped proxy result back into the kind of HTTP response the original provider would have returned. It also handles large or non-JSON binary results by returning a redirect to a stored file URL.

**Data flow**: It receives a decoded dictionary from Composio plus the original request. First it unwraps any nested `data` layers until it reaches the provider-like status, headers, and body. It removes body-specific headers that would be wrong after rebuilding the response. If Composio reports binary data, it checks for a presigned download URL and returns a 302 redirect with that URL in the `location` header. Otherwise, it converts dictionaries and lists to JSON bytes, strings to text bytes, missing data to an empty body, and returns a reconstructed HTTP response.

**Call relations**: `handle_async_request` calls this after a successful Composio proxy response has been read and decoded. It is the final translation step that hides Composio’s envelope from the connector using the transport.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport used to talk to Composio. It is the cleanup step for network resources such as open connections.

**Data flow**: It receives no new data. It delegates closing to the inner transport stored on the proxy transport. After it finishes, the underlying network transport has been told to release its resources.

**Call relations**: This fits at the end of a transport’s lifetime. `ComposioRequestForwarder.forward` creates a temporary `ComposioProxyTransport` for a single forwarded request and makes sure this cleanup method runs afterward.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a command-line or broker-style path. It uses the same safe proxy rewriting as the transport, but wraps the whole exchange in a hard timeout and response size cap.

**Data flow**: It receives an account id, HTTP method, target URL, headers, and raw request body. It gets the configured Composio client, builds a `ComposioProxyTransport` for that account, creates an HTTP request with a timeout, and runs the request through the transport. It reads the returned response body, closes the transport afterward, and returns a `ForwardedResponse` containing the status, headers, and body. If the whole operation takes too long, it raises a Composio timeout error instead of waiting forever.

**Call relations**: This is the one-shot forwarding path. It constructs `ComposioProxyTransport`, then calls its request handling path to do the actual Composio proxy execution. After the response is read, it packages the result into `ForwardedResponse` for the broker or proxy layer that asked for the forward.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### Sandbox egress policy
Turns credentials, grants, model settings, manifests, and storage destinations into enforceable outbound network and secret-injection rules for sandboxed runs.

### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `turn setup / request policy derivation`

A sandbox is meant to run useful work without freely leaking data or credentials to the internet. This file is the rule factory for that boundary. It does not open connections itself. Instead, it builds small rule objects that say things like: “this exact host is allowed,” “replace this harmless placeholder with the real secret,” “count requests to this host,” or “send this request through the broker instead of directly upstream.”

The main idea is that the sandbox sees sentinels, which are fake secret values, rather than raw credentials. When a request leaves the sandbox, the proxy can swap the sentinel for the real key only for the right host and header. This is like giving a courier a sealed envelope at the last checkpoint instead of letting everyone in the building see what is inside.

The file derives rules from several sources. The selected AI model allows and meters its provider host. Extension manifests may allow public internet for live turns. S3-backed artifact sharing allows only the storage host needed for presigned file uploads. Credential slots add per-workspace host access and header injection when a stored secret is available. Grants add connector provider and file-transfer hosts, while CLI credentials can be forwarded through the broker so tokens never land in the sandbox. If one credential slot fails, this file logs a warning and skips only that slot, keeping the rest of the run usable.

#### Function details

##### `provider_host`  (lines 91–95)

```
def provider_host(model: str) -> str
```

**Purpose**: Finds which model provider host should be used for a model name. For example, model names starting with OpenAI-style prefixes map to OpenAI’s API host, while Claude-style names map to Anthropic’s host.

**Data flow**: It receives a model name as text, checks it against known prefixes, and returns the matching API host. If no prefix matches, it raises an error because the proxy would not know which provider to allow.

**Call relations**: derive_model_rules calls this first when building model access rules. The returned host then decides which authentication header and metering rules are created for the model provider.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 98–113)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: Builds the network rules needed for the sandbox to call the selected AI model provider. It allows the provider host, arranges safe key injection, and marks model traffic for token metering.

**Data flow**: It takes a model name and the real provider key. It looks up the provider host, chooses the correct authorization header shape, creates a host allow rule, creates an injection rule that replaces the model-key sentinel with the real key, and creates a meter rule for token usage. It returns these rules as a tuple.

**Call relations**: This function relies on provider_host to identify the provider. It then creates ScopeRule, InjectionRule, and MeterRule objects that the egress proxy can later read when the sandbox tries to contact the model API.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 116–118)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: Checks whether any extension manifest says the sandbox needs general internet access during live turns. If so, it adds a rule that permits public internet access under the proxy’s controls.

**Data flow**: It receives all manifests for the deploy, scans their sandbox_internet setting, and returns one InternetRule if any manifest asks for it. If none do, it returns an empty tuple.

**Call relations**: This rule derivation is part of the larger policy-building step. It creates InternetRule only when manifests justify wider internet access, so other rule builders can stay narrowly scoped to exact hosts.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 121–137)

```
async def derive_artifact_store_rules(blob: BlobStore) -> tuple[Rule, ...]
```

**Purpose**: Allows sandbox file sharing when the artifact store uses S3-style network storage. Without this, a sandbox could create a file but be blocked when trying to upload it through a presigned storage URL.

**Data flow**: It receives the blob store object. If the store is an S3BlobStore, it asks the store for its upload host, then returns a host allow rule and a request-metering rule for that host. For non-network storage backends, it returns no rules.

**Call relations**: This function calls the blob store’s put_host method only for S3-backed storage. It hands back ScopeRule and MeterRule objects so the proxy can allow exactly the artifact-storage host without turning on broad public internet.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 140–203)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: Builds rules for workspace-specific credential slots, so tools can use stored secrets without those secrets appearing inside the sandbox. It also makes sure each credential host is allowed and, when configured, metered.

**Data flow**: It receives credential slot declarations, a workspace ID, and a credential store. For each slot that has an injection target, it tries to read the real secret and resolve the intended host for that workspace. If either is missing or fails, it logs a warning when appropriate and skips that slot. For usable slots, it creates injection rules, groups them by host, adds one host allow rule per host, and adds metering when the slot declares a dimension. Git Basic authentication slots are specially encoded into the Basic header format before injection. The result is a tuple of rules.

**Call relations**: This function calls slot_secret and credential_host to turn a declaration into a real per-workspace rule. It creates InjectionRule, ScopeRule, and MeterRule objects. It also calls warn when a slot cannot be safely resolved, deliberately continuing so one broken credential does not block unrelated egress rules.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 206–223)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: Builds allow-and-meter rules for active connector grants. A grant lets the sandbox reach the connector’s provider host and any needed broker file-transfer hosts, but it does not inject a secret because the broker keeps and uses the account token server-side.

**Data flow**: It receives grants and, optionally, a ConnectorTransferHosts lookup. For each grant, it combines the grant’s provider host with any extra transfer hosts, removes blanks and duplicates, then creates one allow rule covering those hosts and one request-metering rule per host. It returns all those rules as a tuple.

**Call relations**: When transfer_hosts is provided, this function asks ConnectorTransferHosts.of for the extra hosts belonging to the grant provider. It then creates ScopeRule and MeterRule objects that let the proxy admit connector-related traffic while keeping it counted.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 226–246)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: Builds forwarding rules for connector CLI credentials. These rules let approved sentinel-carrying requests go through the broker, where the real account credential can be applied without exposing it to the sandbox.

**Data flow**: It receives grants, the acting member ID if there is one, and a mapping of provider names to CLI credential definitions. It keeps only grants whose provider has a CLI credential and whose account the acting member may use: either shared grants or grants owned by that member. For each accepted grant, it creates a ForwardRule containing the host, header, sentinel, account ID, and broker forwarder.

**Call relations**: This function calls grant_sentinel to compute the placeholder value expected in outbound requests, then creates ForwardRule objects. It fits beside derive_grant_rules: grant rules allow and meter hosts, while CLI rules describe when traffic should be executed through the broker.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 260–261)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Looks up the broker file-transfer hosts for a connector provider. If the provider was explicitly listed, it uses that exact list; otherwise it falls back to the default hosts from the open connector namespace.

**Data flow**: It receives a provider name, checks the explicit provider-to-hosts mapping, and returns the matching tuple of hosts. If there is no explicit entry, it returns the stored default tuple.

**Call relations**: derive_grant_rules calls this when it needs to know which extra file-transfer hosts a grant should allow. This small lookup keeps the grant-rule code independent from how those hosts were gathered from manifests.


##### `connector_transfer_hosts`  (lines 264–275)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: Collects connector file-transfer host settings from extension manifests into a lookup object. This gives grant-rule creation a single place to ask which transfer hosts belong to each provider.

**Data flow**: It receives all manifests, walks through their registered connectors, and builds an explicit mapping from provider name to that connector’s declared transfer hosts. It also asks for the open connector namespace and uses its transfer hosts as the default for providers not explicitly registered. It returns a ConnectorTransferHosts value containing both pieces.

**Call relations**: This function calls open_connector_namespace to find the default namespace settings, then constructs ConnectorTransferHosts. The resulting object is passed to derive_grant_rules, which uses ConnectorTransferHosts.of while building grant-related egress rules.

*Call graph*: 2 external calls (__init__, open_connector_namespace).

## 📊 State Registers Touched

- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-audience-policy` — The saved visibility rules that decide which people may see or use a conversation or agent.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-egress-policy` — The network access rules that decide which outside sites sandboxed work may contact and which secrets may be injected.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-source-sync-state` — The saved state of external sources, synced pages, deletion markers, cursors, and retry backoff.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-onboarding-claims` — The hosted signup state for email claims, invitations, company-domain workspace mapping, and temporary access tokens.
- `reg-oauth-handshake-state` — Short-lived OAuth/connection callback state such as nonce, redirect intent, and verifier data used to bind an external authorization return to the initiating workspace, member, agent, and grant before saving the connection.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-secret-handoff-state` — Short-lived private handoff state used to bind sensitive credential or connection setup payloads to the right actor before secrets are accepted and stored.

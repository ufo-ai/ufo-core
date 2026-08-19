# Credential storage, declaration, and injection  `stage-20.3`

This stage is shared behind-the-scenes support for handling secrets, such as API keys and access tokens. Its job is to let the system know what credentials exist, store their real values safely, and provide them only to trusted work that needs them.

The credential kind file acts like a public label board. Extensions can declare that they need a credential slot, and users can see whether that slot is empty or filled. The secret itself is never shown there. The credentials file is the locked safe. It encrypts secret values before saving them, checks sealed short-lived update requests before accepting changes, and makes sure a secret belongs to the right workspace and user flow.

When sandboxed tools need access, the sandbox environment file prepares safe environment variables. These are named settings passed into the sandbox process. It gives tools approved access without copying raw secrets into the sandbox. The direct source connector file uses the same safe store when a connector needs a member’s own API key, turning it into an authentication token for feed syncing.

## Files in this stage

### Credential Declaration and Storage
Defines credential slots as safe workspace-visible objects and manages encrypted storage, sealed updates, and controlled secret retrieval.

### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling`

Some extensions need a workspace to provide outside secrets, such as API keys. This file represents those needs as “credential” objects: not the secret itself, but the empty-or-filled slot where the secret belongs. Think of it like a row of labeled locked boxes. Everyone can see the labels and whether a box has something inside, but nobody can see through the box.

The slot declarations come from installed extension manifests. The database only stores a row when a value has been sealed and saved. If there is no row, the slot is still listed, but it is shown as empty. Reads return the slot name, description, extension name, and any host information needed for injection, plus timestamps when a stored value exists. They never return the secret, and not even a digest or fingerprint of it.

The file also protects how credentials change. Create and update are refused because filling or rotating a secret must happen through `request_credentials`, a separate private handoff. Delete is allowed only for workspace admins and simply removes the stored value, so the declared slot remains visible as empty. This keeps credential visibility useful while keeping credential contents out of normal object reads.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace’s credential slots as a paged list. It is used when a caller wants an overview of all declared slots and whether each one is filled.

**Data flow**: It receives a tool context and a list query. It asks `_rows` to build one safe summary row per declared slot, then passes those rows and the query into the paging helper. The result is an `ObjectPage` containing only metadata such as slot name, extension, and filled state.

**Call relations**: This is the main list path for tool-style object access. It relies on `_rows` to combine extension declarations with database fill state, then hands the finished rows to `object_page` so filtering, ordering, or paging can be applied consistently with other object kinds.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the same safe credential-slot list to a signed-in workspace member through the member-facing portal. It does not narrow the list by member, because these declarations are workspace-wide and contain no secret values.

**Data flow**: It receives optional extension context, the member’s id and admin flag, and a list query. It builds the same rows as the regular list view, then turns them into a paged result. The output is a page of safe slot summaries for that workspace.

**Call relations**: This is the portal-facing counterpart to `CredentialObjects.list`. It calls `_rows` for the shared row-building work and then `object_page` for the final page, so members and tools see the same declared slots and fill state.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the safe detail view for one credential slot. It is used when a caller wants the declaration for a specific slot, not the stored secret.

**Data flow**: It receives a tool context and a slot name. It asks `_detail` to look up the declared slot and any database timestamps. It returns an `ObjectDetail` if the slot is declared, or `None` if no active manifest declares that slot.

**Call relations**: This is the normal object-detail read path. It delegates the real work to `_detail`, which is shared with the member-facing detail view so both paths expose the same safe information.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns one credential slot as a signed-in member sees it: the list row plus the detailed declaration. It is used by the portal when someone opens an individual credential slot.

**Data flow**: It receives optional extension context, a slot name, member information, and admin status. It first asks `_detail` for the slot’s safe declaration. If the slot is unknown, it returns `None`; otherwise it also builds the row list, finds the matching row, and returns both row and detail inside a `MemberObject`.

**Call relations**: This combines the two shared read helpers. `_detail` answers whether the slot exists and what its declaration says, while `_rows` supplies the summary row with filled-or-empty state. The function then wraps both pieces into the member-facing object shape.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current safe status of one credential slot, mainly whether it is filled. If the slot uses a host value that can be resolved safely, it may also include that host.

**Data flow**: It receives a tool context, slot name, and an optional expected generation value. It checks the declared slots by name. If the slot is unknown, it returns `None`. If known, it queries the credential table for the current workspace to see whether a row exists, then returns `{'filled': true}` or `{'filled': false}`. When host information is configured and a credential store is available, it also asks the credential system for the resolved host and includes it.

**Call relations**: This is a lightweight status read. It uses `_named` to validate the slot against extension declarations, `workspace_tx` and `ws_current` to query only the current workspace’s database rows, and `credential_host` when host information must be supplied without exposing the credential value.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create, fill, or update a credential through the ordinary object apply path. This protects secrets from being passed through a general-purpose object write interface.

**Data flow**: It receives the requested slot name, proposed spec, old spec, context, and optional expected generation. Instead of saving anything, it immediately raises a `VerbNotSupported` error with a message telling the caller to use `request_credentials`.

**Call relations**: This function is the safety gate for create and update operations on the credential object kind. Rather than calling storage code, it stops the flow and points callers toward the private credential handoff designed for secrets.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot, but only if the speaker is a workspace admin. The slot itself remains declared and will still appear in lists as empty.

**Data flow**: It receives a tool context, slot name, and optional expected generation. It asks the context whether the speaker is an admin. If not, it raises `AdminRequired`. If yes, it finds the declared slot, opens a workspace database transaction, and deletes the credential row for that workspace and slot. Nothing is returned.

**Call relations**: This is the allowed destructive write path. It uses `_named` to map the public name to a declared slot, checks authorization through `ToolContext.speaker_is_admin`, and then uses the current workspace and database transaction to remove only that workspace’s sealed credential row.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–179)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe summary rows used by both tool and member list views. Each row says what the slot is for, which extension declared it, and whether it is currently filled.

**Data flow**: It asks `_filled_slots` for the set of slot names that have stored credential rows. It asks `_named` for all declared slots. For each declared slot, sorted by name, it creates an `ObjectRow` with a readable summary and fields for extension name and filled state. It returns all rows as a tuple.

**Call relations**: This is the shared row factory behind `list`, `member_page`, and part of `member_detail`. It sits between raw declarations plus database state and the public object-list format, making sure every read path presents credentials the same safe way.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 181–205)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detail record for one declared credential slot. It includes the declaration and timestamps, but never reads or returns the secret value.

**Data flow**: It receives a slot name. It looks up the slot declaration with `_named`; if none exists, it returns `None`. If the slot exists, it queries the current workspace’s credential row for creation and update times. It then creates a `CredentialSpec` from the manifest declaration, including description, extension, and host information, and wraps it in an `ObjectDetail` with timestamps if a stored row exists.

**Call relations**: This helper is shared by `get` and `member_detail`. It uses the current workspace transaction for timestamp lookup and constructs the safe object-detail shape that both normal tool reads and portal reads can return.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 207–208)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the file’s list of declared credential slots into a dictionary keyed by slot name. This makes later lookups simple and consistent.

**Data flow**: It reads `self.slots`, the declared slots gathered from active extension manifests. It passes them to `named_slots`, which organizes them by name. It returns that name-to-slot mapping.

**Call relations**: This is a small shared lookup helper. `status`, `delete`, `_rows`, and `_detail` all call it before working with a named slot, so they all interpret slot names the same way.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 210–219)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the current workspace. It only returns slot names, not the stored credential contents.

**Data flow**: It opens a workspace database transaction, reads the credential table for rows belonging to the current workspace, and collects their slot names. It returns those names as an immutable set, so callers can quickly ask whether a declared slot is filled.

**Call relations**: This helper feeds `_rows`. `_rows` needs to combine extension-declared slots with database fill state, and `_filled_slots` supplies the database side of that comparison without exposing any credential values.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting: startup setup, credential prompts, provider callbacks, sandbox opening, and proxy credential injection`

This file is the credential safety box for the project. A workspace may need private values such as API keys, OAuth-style provider bindings, or a chosen account host. The sandbox should never see the real secret directly; it sees a placeholder, and the proxy swaps in the real value only at the edge where it is needed.

The file does three main jobs. First, it turns declared credential slot names into stable object names, so the rest of the system can refer to them consistently. Second, it creates and opens encrypted “seals” for credential actions. A seal is like a tamper-proof ticket: it says which workspace, member, slot, and purpose the action is for, and it expires when appropriate. This prevents a value typed for one slot, user, or workspace from being reused somewhere else. Third, it stores credential values in the database encrypted with Fernet, a symmetric encryption tool where the same secret key locks and unlocks data.

It also supports credentials that are not simply typed by a member. Some providers mint short-lived secrets from a stored binding, and some providers let a workspace choose from a fixed list of hosts. The important pattern is that every consumer asks the same questions here: “is this slot set?”, “what secret should be used?”, and “which host is allowed?” That keeps the sandbox export, proxy rules, and provider integrations from disagreeing.

#### Function details

##### `credential_object_name`  (lines 28–31)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a simple lowercase object name that can be used consistently elsewhere. It removes punctuation-like clutter by replacing non-letter-or-number runs with dashes.

**Data flow**: It receives a slot name as text → lowercases it, replaces unsupported characters with dashes, and trims extra dashes from the ends → returns the cleaned name.

**Call relations**: When slot names are being prepared for the rest of the system, named_slots calls this first to get the plain shared name for each declared slot.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `named_slots`  (lines 34–49)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Builds the public object-name map for declared credential slots. If two slots would clean up to the same name, it adds a short stable fingerprint so both can still be addressed safely.

**Data flow**: It receives all declared slots → groups them by their cleaned object name → keeps unique names as-is, and gives colliding names a short digest based on extension and slot name → returns a dictionary from object name to slot declaration.

**Call relations**: It relies on credential_object_name to create the first version of each name, and uses hashing only when that simple name would collide.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 93–94)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Encrypts a credential request state into a sealed string. Other parts of the system can hand this string around without exposing the private details inside.

**Data flow**: It receives a Fernet encryption helper and a CredentialRequestState → turns the state into JSON text → encrypts it → returns the encrypted text seal.

**Call relations**: CredentialRequests.seal, CredentialRequests.authorize, and seal_installation call this whenever they need a tamper-proof credential ticket for later verification.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 97–123)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Opens and checks an encrypted credential seal. It rejects seals that are expired, forged, malformed, or meant for a different purpose.

**Data flow**: It receives a Fernet helper, a sealed string, the expected purpose, and optionally a time limit → decrypts and parses the sealed state → compares the embedded purpose with the expected one → returns the trusted state or raises CredentialRequestInvalid.

**Call relations**: CredentialRequests.open_authorization, authorized_slot_workspace, and open_installation call this before trusting any sealed credential action. It centralizes the safety checks so each caller does not have to remember them.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 142–155)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a short-lived sealed request that allows a member to privately fill specific credential slots. It refuses slots that no installed extension declared or that members are not allowed to type manually.

**Data flow**: It receives workspace ID, member ID, and requested slot names → checks the slots against the declared and fillable sets → packages the allowed request into a CredentialRequestState → returns an encrypted seal.

**Call relations**: This is used when the system asks a member to provide credential values outside the chat transcript. It hands off to seal_credential_request after doing the slot permission checks.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 157–170)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization request for a provider flow, such as an OAuth-style redirect. Unlike manual filling, this can target slots written by deployment machinery rather than typed by the member.

**Data flow**: It receives workspace ID, member ID, one slot, and provider state text → checks that the slot is declared and the provider state is present → seals those details into an encrypted string → returns the seal.

**Call relations**: Provider authorization setup calls this before sending a member to an external provider. It uses seal_credential_request so the callback can later prove it belongs to the same request.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 172–186)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a returned provider authorization seal and extracts its provider state. It makes sure the seal belongs to the exact workspace, member, and slot expected.

**Data flow**: It receives a sealed string plus expected workspace, member, and slot → opens the seal → compares the stored claims to the expected values → returns the provider payload if everything matches, otherwise raises an error.

**Call relations**: Provider callback or fulfillment code calls this after an authorization round trip. It depends on open_credential_request for decryption and then adds the more specific workspace/member/slot checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 189–203)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Stores a provider installation ID as a protected binding instead of as bare text. This stops someone from typing or guessing another organization’s installation ID and using it as their own.

**Data flow**: It receives an encryption helper, workspace ID, slot, and installation ID → wraps them in a state marked specifically as an installation binding → encrypts that state → returns the sealed binding.

**Call relations**: Provider installation flows call this when an installation has been authorized. It reuses seal_credential_request but marks the seal with a different purpose from ordinary credential requests.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 206–217)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed provider installation binding and returns the installation ID only if it belongs to the expected workspace and slot.

**Data flow**: It receives an encryption helper, workspace ID, slot, and sealed binding → decrypts the binding without an expiry limit → checks purpose, workspace, slot, and payload → returns the installation ID or raises CredentialRequestInvalid.

**Call relations**: Provider code calls this when it needs to use a stored installation. It delegates the general seal opening to open_credential_request, then confirms the binding is for the exact local context.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 223–229)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Installs the process-wide credential request authority used by routes that cannot receive it through normal request context. This is especially useful for provider browser callbacks.

**Data flow**: It receives a CredentialRequests object, or None if credential support is unavailable → saves it in a module-level variable → returns nothing, but changes what later global lookups will find.

**Call relations**: Startup code calls this once when the server is configured. Later, installed_credential_requests and authorized_slot_workspace read the installed value.


##### `installed_credential_requests`  (lines 232–235)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or fails clearly if no credential key was configured. It gives callback code access to the Fernet key and declared slot rules.

**Data flow**: It reads the module-level installed CredentialRequests value → if present, returns it → if absent, raises a runtime error explaining that credential authorization is unavailable.

**Call relations**: Code that needs the installed credential authority calls this after startup has had a chance to run install_credential_requests.


##### `authorized_slot_workspace`  (lines 238–254)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Finds which workspace an authorization seal belongs to, but only if the seal matches one exact slot and provider payload. It returns None instead of throwing for invalid or unrelated seals.

**Data flow**: It receives a sealed string, expected slot, and expected payload → uses the installed Fernet key to open the seal → checks that the seal names exactly that slot and payload → returns the workspace ID, or None if anything does not match.

**Call relations**: Provider callback routes use this when the browser returns without a normal session or turn context. It calls open_credential_request and uses the globally installed CredentialRequests set by install_credential_requests.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 261–283)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Encrypts and saves a credential value for one workspace and slot. It updates an existing row or inserts a new one, so callers do not need to care whether the slot was already present.

**Data flow**: It receives workspace ID, slot name, and plaintext secret → rejects an empty secret → encrypts the secret → opens a workspace database transaction → updates the matching credential row, or inserts one if none existed → returns nothing.

**Call relations**: Fulfillment and provider-binding code use this after a credential value has been safely accepted. It uses the database transaction helper and SQL insert/update operations to write the encrypted value.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 285–297)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Retrieves and decrypts the stored credential for one workspace and slot. If no value exists, it raises a specific error so callers can distinguish “unset” from other failures.

**Data flow**: It receives workspace ID and slot name → queries the credential table for the encrypted value → if missing, raises CredentialSlotUnset → otherwise decrypts the ciphertext and returns the plaintext secret.

**Call relations**: slot_secret, slot_is_set, credential_host, and GitHub provider code call this when they need a stored value. It is the shared read path for encrypted credentials.

*Call graph*: called by 7 (credential_host, slot_is_set, slot_secret, bound, secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 299–330)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still contains an expected old value. This prevents two concurrent refreshes from accidentally overwriting a newer token with an older one.

**Data flow**: It receives workspace ID, slot, expected current plaintext, and replacement plaintext → rejects an empty replacement → reads and decrypts the current stored value → if it is missing or different from expected, returns false → otherwise writes the encrypted replacement only if the database row is still unchanged → returns whether the update succeeded.

**Call relations**: OAuth-style provider clients use this after refreshing a token. It uses the same encrypted store as put and get, but adds a compare-before-replace safety check.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 354–359)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host-choice declaration is internally valid. The default host must be one of the allowed hosts, otherwise the system could fall back to a host it never meant to allow.

**Data flow**: After a HostChoice is created, it reads its default value and allowed host list → if the default is missing from the list, it raises ValueError → otherwise the object remains usable.

**Call relations**: This runs automatically when a HostChoice instance is constructed. It protects later calls to credential_host and HostChoice.resolve from working with an invalid declaration.


##### `HostChoice.resolve`  (lines 361–366)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string. It accepts different letter casing, but only returns a host from the fixed allowed list.

**Data flow**: It receives the stored selection text → trims spaces and lowercases it for comparison → searches the declared hosts case-insensitively → returns the canonical declared host, or None if the selection is not allowed.

**Call relations**: credential_host calls this after reading a workspace’s stored host choice. This keeps member-provided text from becoming a free-form network destination.


##### `CredentialSource.secret`  (lines 374–374)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the interface for credential sources that can mint a secret on demand instead of simply reading one from storage. Implementations use this for provider-generated or short-lived credentials.

**Data flow**: An implementation receives a workspace ID and the credential store → may read stored bindings or contact a provider → returns a secret string, or None if there is nothing to mint.

**Call relations**: slot_secret calls this when a slot has a CredentialSource. Concrete provider classes, such as GitHub authentication helpers, supply the real behavior behind this protocol method.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 376–384)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the interface for asking whether a workspace has enough provider binding to mint a secret, without actually minting one. This avoids unnecessary provider calls during routine sandbox setup.

**Data flow**: An implementation receives a workspace ID and credential store → checks local binding information or other cheap state → returns true or false, or raises if the binding exists but cannot be used.

**Call relations**: slot_is_set calls this when it needs to know whether a credential slot should count as available. Provider implementations make this answer consistent with what secret would later do.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 387–402)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the central question: what secret, if any, should this slot provide for this workspace? It prefers a minted provider secret when a source exists, and otherwise falls back to the stored member value.

**Data flow**: It receives slot name, optional credential source, workspace ID, and store → asks the source to mint a secret if present → if no minted secret appears, tries to read the stored value → returns the secret string or None if the slot is unset.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers use this shared path so they all agree on the value for a slot. It calls CredentialSource.secret and CredentialStore.get.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 405–423)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a credential slot would yield a usable secret, without producing that secret. This is useful when deciding whether to configure a sandbox client at all.

**Data flow**: It receives slot name, optional credential source, workspace ID, and store → if a source exists, asks whether it is bound → if not bound or no source exists, checks whether a stored value is present → returns true or false, while letting provider errors surface.

**Call relations**: Sandbox setup and rule derivation use this cheap availability check before any actual secret injection. It calls CredentialSource.bound for provider-backed slots and CredentialStore.get for stored values.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 426–443)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Resolves the network host that a credential is allowed to be used with. The host may be fixed by code, or chosen by the workspace from a closed list with a safe default.

**Data flow**: It receives the credential store, workspace ID, and either a plain host string or a HostChoice → if it is a plain string, returns it → if it is a HostChoice, reads the workspace’s selected value, falls back to the default if unset, and returns only a declared host or None.

**Call relations**: The proxy and engine use this to agree on where credential injection is allowed. It calls CredentialStore.get for saved host choices and HostChoice.resolve to reject values outside the declared list.

*Call graph*: calls 1 internal fn (get).


### Credential Injection and Direct Auth
Uses stored credentials to prepare sandbox execution environments and authenticate direct source connectors without exposing raw secrets.

### `core/src/ufo/sandbox/exec_env.py`

`domain_logic` · `sandbox open for probes and command execution`

A sandbox is a controlled place where the system can run commands for a conversation. Those commands often need access to outside services: a git host, a connector CLI, or a provider such as a monitoring service. This file prepares the safe “labels” the sandbox receives for that access. The important safety idea is that the sandbox does not receive actual passwords or API keys. It receives sentinels: placeholder strings that an outgoing network proxy recognizes and swaps for the real secret only when a permitted request leaves the sandbox.

The main class, ProbeEnv, collects the pieces needed for an off-turn probe: the conversation id, git configuration, connector CLI credentials, and provider environment variables. It looks at the current workspace, checks which credential slots are actually filled, resolves any provider host choices, and skips anything that is missing or unsafe to export. If a credential lookup fails, it warns and continues rather than breaking the whole sandbox open.

For git, the file uses Git’s special environment-based configuration format, because the sandbox may not have a writable git config file. For connector CLIs, it chooses either the acting member’s private grant or a shared workspace grant. If there are multiple possible accounts and a single static environment variable cannot say which one was intended, it logs the ambiguity and exports nothing for that CLI. That avoids silently using the wrong account.

#### Function details

##### `ProbeEnv.exports`  (lines 60–74)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None=None) -> dict[str, str]
```

**Purpose**: Builds the complete set of environment variables to give to a probe sandbox. It combines conversation identity, git access settings, connector CLI sentinels, and keyed provider sentinels into one dictionary.

**Data flow**: It receives a conversation id, a probe id, and optionally the member the probe is acting as. It reads the current workspace id, then asks helper functions to prepare git configuration, git credential headers, connector CLI variables, and keyed provider variables. It returns one environment-variable dictionary ready to pass into the sandbox; it does not mutate the credential or grant stores.

**Call relations**: This is the top-level assembly point in the file. When a probe needs to open a sandbox, it calls this method, which then delegates the specialized work to _git_config_env, _git_credential_config, _grant_cli_env, and _keyed_provider_env. It also calls ws_current to find which workspace’s credentials and grants should be considered.

*Call graph*: calls 4 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env); 1 external calls (ws_current).


##### `_git_config_env`  (lines 77–84)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns a list of git settings into the environment-variable format that git understands. This lets the sandbox configure git without writing a config file.

**Data flow**: It receives pairs such as a git setting name and its value. It counts them, then creates variables like GIT_CONFIG_COUNT, GIT_CONFIG_KEY_0, and GIT_CONFIG_VALUE_0. It returns a dictionary that git can read directly from the process environment.

**Call relations**: ProbeEnv.exports calls this after collecting the fixed git proxy setting and any credential-related git settings. This helper does only formatting: it does not decide which credentials exist or which hosts are allowed.

*Call graph*: called by 1 (exports).


##### `_git_credential_config`  (lines 87–122)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Prepares git authentication settings for every declared credential slot that is both relevant to git and actually filled for the workspace. The values it creates contain sentinels, not real passwords.

**Data flow**: It receives the credential store, the declared credential slots, and the workspace id. For each slot, it checks whether the slot has a git basic-auth target and whether the workspace has stored a credential for it. It resolves the host, builds a git extra-header setting containing the slot’s sentinel, and returns all such settings as a tuple. If the credential store is absent, a slot is unset, or the host cannot be resolved, it skips that slot; lookup errors are turned into warnings.

**Call relations**: ProbeEnv.exports calls this before formatting git environment variables. This helper relies on slot_is_set to avoid exporting unusable sentinels, credential_host to find the right host name, and warn to record recoverable problems without stopping the sandbox from opening.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 125–167)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for provider credentials that are meant to be used through the sandbox’s network proxy. It gives the sandbox placeholder sentinels and, when needed, the resolved provider host.

**Data flow**: It receives the credential store, declared credential slots, and workspace id. It walks through each slot, looks for declared environment-variable targets or host environment variables, checks that the workspace has a stored credential, and resolves the chosen host. For usable slots, it adds the provider’s secret environment variable with the sentinel value and optionally adds a host environment variable with the real host name. It returns the completed environment dictionary, skipping missing or invalid slots and warning on lookup failures or unavailable hosts.

**Call relations**: ProbeEnv.exports calls this as one part of the sandbox environment. Like _git_credential_config, it uses slot_is_set and credential_host so that only working, declared credentials are exported. Its warnings help explain why a provider variable may not appear in a sandbox.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 170–212)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, run_id: UUID) -> dict[str, str]
```

**Purpose**: Chooses which connector CLI credentials a sandbox should receive and exports them as sentinel-valued environment variables. It prefers the acting member’s own connected account, then falls back to a shared workspace connection.

**Data flow**: It receives the grant store, the known CLI credential declarations, the acting member id, and the run id. It reads the active grants, groups them by provider, chooses private grants for the acting member before shared grants, and writes the matching CLI environment variable to a grant sentinel. If more than one account could match a single CLI variable, it logs the ambiguity and exports nothing for that provider. It returns the environment variables for unambiguous CLI access.

**Call relations**: ProbeEnv.exports calls this when assembling the full sandbox environment. This helper gets current grant data through GrantStore.active_grants, converts a chosen account id into a safe sentinel with grant_sentinel, and uses log to record cases where picking an account automatically would be unsafe.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 2 external calls (grant_sentinel, log).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `feed-sync authentication`

Some data sources need an API key that the system cannot get from a normal account broker, or that a deployment wants to keep under its own control. This file supports that “bring your own key” model. The key is stored encrypted under the provider’s name, and when a source is configured to use the special direct account route, its sync work is sent here.

The important safety rule is that the secret stays on the host side. The sync job is allowed to read it through `CredentialAccess`, which is the workspace-limited object for reading declared credential slots. The key is decrypted inside the running job process and used only to authenticate outgoing provider HTTP requests. It is not passed into the sandbox, exposed to an agent, or logged.

The file contains one small frozen data class, `DirectAuthProxy`. Think of it like a locked key cabinet clerk: given a provider name, it opens only that provider’s declared slot, takes out the API key, and wraps it in the standard `Credential` shape as a bearer token. The `workspace_id` and `account` are part of the shared auth-proxy interface, but in this backend the actual secret is found by provider name, not by the account handle.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method fetches the member-added API key for a provider and returns it in the standard credential format used by source sync code. It exists so direct-auth sources can authenticate provider requests without sending the secret outside the host-side job.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. It uses the provider name to read the matching secret from `self.credentials`, then wraps that secret as a bearer credential. The result is a `Credential` object containing the bearer token; the credential store is read, but nothing else is changed.

**Call relations**: When the feed-sync authentication flow routes a direct account to this proxy, this method is the point where the stored provider key is resolved. After reading the secret, it hands it to `Credential` to package it in the common form that the rest of the sync code can use for provider HTTP authentication.

*Call graph*: 1 external calls (__init__).

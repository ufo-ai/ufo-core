# Credential vault, BYOK slots, and controlled secret disclosure  `stage-18.3`

This stage is shared security support that sits behind the main work of the system. Its job is to keep secrets, such as API keys or Git passwords, out of ordinary code paths and reveal them only when a trusted part of the system truly needs them.

The credential slot model in credential_kind.py is like a set of labeled empty lockers. Extensions can declare “bring your own key” slots, meaning places where a user must supply a secret. The workspace can show that a slot exists and whether it has been filled, but it never shows the secret itself.

credentials.py is the vault. It stores user-supplied and provider-issued credentials in sealed form, and it checks short-lived proof tokens before releasing anything. These tokens act like temporary claim tickets tied to the correct workspace, member, and slot.

credential_callback.py is a narrow service door for the sandbox cache daemon. When it needs Git credentials, it calls this private endpoint and receives only the credential already tied to that workspace request, not general access to the vault.

## Files in this stage

### Credential Slot Modeling
Defines BYOK credential slots as visible workspace objects while keeping secret values hidden.

### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling`

This file is about “bring your own key” credentials: secret values that an installed extension says it needs, such as an API token. The important idea is that the extension declares the slot, but the stored database row only exists when someone has filled that slot with a sealed secret. So the system must show both filled and empty slots, even though empty ones have no database row.

Think of it like labeled safety deposit boxes. The labels come from the extension manifest: “this box is for Extension X’s token.” The vault may or may not contain something, but visitors are only allowed to see the label and whether the box is occupied, never the contents.

`CredentialObjects` provides the object-style view of these slots. It can list all declared slots, show details for one slot, report whether it is filled, and let an admin clear a stored value. It refuses normal create or update operations, because filling or rotating a secret must happen through `request_credentials`, a separate private handoff designed for sensitive data. Deleting does not remove the declaration; it only removes the stored secret row, so the slot remains visible as empty. Both tool-side reads and member portal reads see the same workspace-wide declaration and fill state.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a paged list of all credential slots declared by active extensions, including whether each slot is filled or empty. This is the general object-list view for tools.

**Data flow**: It receives a tool context and a list query with paging, sorting, or filtering choices. It asks `_rows` to build the full set of visible credential rows, then passes those rows through `object_page` to shape them into the requested page. The result is an `ObjectPage` containing only safe metadata, never any secret value.

**Call relations**: When the object system needs to list credential objects, it calls this method. This method delegates the real row-building work to `_rows`, then hands the rows to the shared paging helper so credential listings behave like other object listings.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows signed-in workspace members the same safe list of credential slots: every declared slot and whether it is filled. It does not narrow the view per member, because credential declarations are workspace-wide and reveal no secret values.

**Data flow**: It receives optional extension context, the member identity, whether the member is an admin, and the list query. It builds rows through `_rows`, applies the query through `object_page`, and returns a page of slot summaries. Member and admin inputs do not change the visible rows here.

**Call relations**: The member portal calls this when a user browses credential slots. Like `list`, it relies on `_rows` for the safe slot summaries and on `object_page` for paging and filtering.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the safe detail view for one credential slot, if that slot is declared. The detail describes the slot and its host requirements, but not the secret.

**Data flow**: It receives a tool context and a slot name. It asks `_detail` to look up the declared slot and any stored timestamps. It returns an `ObjectDetail` when the slot exists, or `None` when no active extension declares that name.

**Call relations**: Tool-side object reads call this for a single credential. It is a thin doorway into `_detail`, which contains the shared lookup and rendering logic also used by the member detail view.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns the member portal’s safe view of one credential slot: the list-style row plus the detailed declaration. It lets a signed-in member inspect what a slot is for and whether it is filled, without seeing the secret.

**Data flow**: It receives optional extension context, a slot name, member identity, and admin status. It first asks `_detail` for the slot declaration and timestamps; if the slot is unknown, it returns `None`. If it exists, it also gets the current rows from `_rows`, finds the matching row, and combines the row and detail into a `MemberObject`.

**Call relations**: The member portal calls this when a user opens one credential slot. It reuses `_detail` for the declaration and `_rows` for the fill-state summary, then packages both pieces together for the portal.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current safe status of one credential slot, mainly whether it has a stored value. If the slot’s host is tied to stored credential information, it can also include the resolved host name.

**Data flow**: It receives a tool context, a slot name, and an optional expected generation value. It checks the declared slot names, returns `None` if the slot is not declared, then queries the credential table for a row in the current workspace. It returns a small dictionary such as `filled: true` or `filled: false`; when possible and relevant, it adds a safe `host` value.

**Call relations**: Status checks call this when they need a quick answer about one credential slot. It uses `_named` to confirm the slot exists, reads the current workspace through `ws_current`, queries the database inside `workspace_tx`, and may call `credential_host` to resolve host information without exposing the secret.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create, fill, or update a credential through the normal object write path. This protects secrets by forcing fills and rotations to go through the dedicated private credential request flow.

**Data flow**: It receives the proposed credential spec, the previous spec if any, and write-related context. Instead of storing anything, it immediately raises a `VerbNotSupported` error with an explanation. Nothing is written and no secret is accepted here.

**Call relations**: The object system would call this for create or update operations. This method intentionally stops that path and points users toward `request_credentials`, which is the separate flow designed for sensitive handoff.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot, but only if the current speaker is a workspace admin. The slot remains listed afterward because the declaration still comes from the extension.

**Data flow**: It receives a tool context, a slot name, and an optional expected generation value. It asks the context whether the speaker is an admin; if not, it raises `AdminRequired`. If allowed, it finds the declared slot name and deletes the matching credential row for the current workspace from the database. The result is no returned value, but the stored secret is removed.

**Call relations**: The object system calls this for delete requests. It uses `_named` to translate the visible name to a declared slot, checks permission through `ToolContext.speaker_is_admin`, then performs the database delete inside `workspace_tx` for the workspace from `ws_current`.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–179)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for every declared credential slot. Each row says which extension declared the slot and whether the slot currently has a stored value.

**Data flow**: It first asks `_filled_slots` for the set of slot names that have database rows in the current workspace. It asks `_named` for the declared slots keyed by name. It then creates one `ObjectRow` per declared slot, with a human-readable summary and fields such as `extension` and `filled`. The output is a sorted tuple of rows.

**Call relations**: `list`, `member_page`, and `member_detail` all rely on this helper so every listing-style view uses the same fill-state calculation and wording. It in turn combines declaration data from `_named` with stored-state data from `_filled_slots`.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 181–205)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detail record for one declared credential slot. It includes the declaration, host-selection information, and timestamps if a value is stored, but never the secret itself.

**Data flow**: It receives a slot name and looks it up among declared slots through `_named`. If the slot is not declared, it returns `None`. If it exists, it queries the credential table for creation and update timestamps in the current workspace. It then creates a `CredentialSpec` from the declaration and wraps it in an `ObjectDetail` with timestamps set to `None` when the slot is empty.

**Call relations**: `get` and `member_detail` call this whenever they need the one-slot detail view. It reads declaration information from `_named`, workspace information from `ws_current`, database state through `workspace_tx`, and shapes the result with `CredentialSpec` and `ObjectDetail`.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 207–208)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the stored tuple of declared credential slots into a lookup table keyed by slot name. This gives the rest of the class a simple way to find a slot by its public name.

**Data flow**: It reads `self.slots`, which contains the declared slots collected from active extension manifests. It passes them to `named_slots`, which returns a dictionary from name to declaration. The output is used for lookups and sorted listings.

**Call relations**: Several methods call this before they can work with a slot: `_detail` and `status` use it to decide whether a name exists, `_rows` uses it to list all declarations, and `delete` uses it before clearing a stored value.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 210–219)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the active workspace. It only returns slot names, not the encrypted or sealed credential values.

**Data flow**: It opens a workspace database transaction, reads the current workspace id, and selects the slot column from all credential rows for that workspace. It converts the returned rows into a frozen set of slot names. The output tells callers which declared slots should be marked as filled.

**Call relations**: `_rows` calls this while building list entries. This helper is the bridge between extension declarations, which say what slots exist, and the database rows, which show which of those slots have actually been filled.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### Sealed Credential Vault
Stores protected credentials and issues short-lived proof tokens that bind requests to the correct workspace, member, and slot.

### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting: startup setup, credential prompts, provider callbacks, sandbox opening, and proxy rule resolution`

This file is the project’s safe deposit box for credentials. A credential “slot” is a named place where a workspace can keep a secret needed by an extension, like an API token. The file makes sure those secrets are encrypted before they go into the database, are not accepted when empty, and are only decrypted when code is about to inject them into an outgoing request.

It also protects the process of asking a user for a secret. Instead of putting the secret in chat, the system creates a sealed request, like a tamper-proof claim ticket. A private surface can ask the user for the value, then return it with that seal. The code checks that the seal is still valid, was made by this deployment, names the same workspace and member, and refers to the correct slot.

Some credentials are not typed by users. They come from providers, such as OAuth-style installations. This file can seal those provider bindings too, with a different purpose marker so a user prompt seal cannot be reused as an installation seal.

Finally, it supports provider hosts that vary by account. Instead of trusting free-text hostnames, it only accepts a choice from a declared list. That matters because the proxy may trust these hosts when deciding where secrets may be sent.

#### Function details

##### `credential_object_name`  (lines 28–31)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a simple object-style name that can be used consistently elsewhere. It lowercases the name and replaces groups of non-letter-or-number characters with hyphens.

**Data flow**: It receives a slot name as text. It normalizes the text into a safe, predictable slug by lowercasing it, swapping punctuation or spaces for hyphens, and trimming extra hyphens at the ends. It returns that cleaned name.

**Call relations**: This is a small helper used by `named_slots`. When slot names need to become stable object names, `named_slots` calls this first before deciding whether any names collide.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `named_slots`  (lines 34–49)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Builds the public object name for each declared credential slot, while avoiding ambiguity when two slots would otherwise get the same name. If two slots clean down to the same slug, it adds a short stable digest so each one stays distinct.

**Data flow**: It receives all declared slots. It groups them by the cleaned name from `credential_object_name`. Single slots keep the plain cleaned name. Colliding slots get a short hash based on their extension and original name. It returns a dictionary from final object name to the matching declared slot.

**Call relations**: This function sits above `credential_object_name`: it uses that helper for the first pass, then uses hashing only when needed. Other parts of the system can use the returned names to refer to credential objects without accidentally merging two different slots.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 93–94)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Wraps credential request state into encrypted, tamper-resistant text. This creates the sealed token that can be handed around without exposing or letting others alter its contents.

**Data flow**: It receives a Fernet encryption object and a `CredentialRequestState` containing claims such as workspace, member, slots, purpose, and optional payload. It converts the state to JSON, encrypts it, and returns the encrypted text.

**Call relations**: `CredentialRequests.seal`, `CredentialRequests.authorize`, and `seal_installation` call this whenever they need to create a sealed claim ticket. It is the shared sealing step for member-entered credentials and provider installation bindings.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 97–123)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Decrypts and verifies a sealed credential token. It rejects tokens that are expired, tampered with, malformed, or sealed for the wrong purpose.

**Data flow**: It receives a Fernet encryption object, sealed text, the expected purpose, and optionally a time-to-live limit. It tries to decrypt the text, parse the JSON into `CredentialRequestState`, and compare the stored purpose with the expected one. It returns the validated state, or raises `CredentialRequestInvalid` if anything is wrong.

**Call relations**: `CredentialRequests.open_authorization`, `authorized_slot_workspace`, and `open_installation` all call this before trusting a sealed token. Those callers then add their own checks, such as matching the workspace, member, slot, or provider payload.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 142–155)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request that allows a member to privately fill one or more allowed credential slots. It refuses slots that are not declared by installed extensions, or that users are not allowed to type by hand.

**Data flow**: It receives a workspace ID, member ID, and requested slot names. It checks every slot against the declared set and the fillable set. If the request is allowed, it builds a `CredentialRequestState` and passes it to `seal_credential_request`. The result is sealed text that can later be used to fulfill the private credential prompt.

**Call relations**: This is the member-facing entry into the sealing flow. It hands off the actual encryption to `seal_credential_request`, and the resulting seal is later checked by fulfillment code outside this file before writing the credential.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 157–170)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization request for a provider-backed credential slot. This is used when a provider flow needs to carry private state through a redirect or similar callback.

**Data flow**: It receives a workspace ID, member ID, slot name, and provider payload. It confirms the slot is declared and the payload is not empty. It stores those facts in a `CredentialRequestState`, seals it through `seal_credential_request`, and returns the sealed text.

**Call relations**: This is parallel to `CredentialRequests.seal`, but for provider authorization rather than direct typed secrets. Later, `CredentialRequests.open_authorization` checks this seal before trusting the provider callback state.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 172–186)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Opens a sealed provider authorization and proves it belongs to the expected workspace, member, and slot. If everything matches, it returns the provider payload that was sealed earlier.

**Data flow**: It receives sealed text plus the workspace ID, member ID, and slot that the caller expects. It opens the seal with `open_credential_request`, then compares the stored workspace, member, and slot to the expected values. It also checks that the slot is declared and that a payload exists. It returns the payload, or raises an error if any check fails.

**Call relations**: This is the verification partner to `CredentialRequests.authorize`. It relies on `open_credential_request` for decryption and basic purpose checking, then performs the more specific checks needed before continuing a provider authorization flow.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 189–203)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Seals a provider installation ID so it is bound to one workspace and one slot. This prevents someone from typing or guessing a raw installation ID and using another organization’s provider connection.

**Data flow**: It receives a Fernet encryption object, workspace ID, slot name, and installation ID. It creates a `CredentialRequestState` with a special installation-binding purpose and the installation ID as payload. It encrypts that state with `seal_credential_request` and returns the sealed text.

**Call relations**: This function uses the same sealing machinery as credential requests but marks the seal with a different purpose. `open_installation` later opens and checks this exact kind of seal when provider-backed credentials need the stored installation ID.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 206–217)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed provider installation binding and confirms it belongs to the expected workspace and slot. It rejects forged text, raw IDs, wrong-purpose seals, and bindings from another workspace.

**Data flow**: It receives a Fernet encryption object, workspace ID, slot name, and sealed text. It opens the text with `open_credential_request`, using the installation-binding purpose and no expiry limit. It checks the workspace, slot, and payload. It returns the installation ID payload if valid.

**Call relations**: This is the verification partner to `seal_installation`. It depends on `open_credential_request` for decryption and purpose checking, then adds the workspace and slot checks needed before a provider source can use the installation.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 223–229)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Stores the process-wide credential request authority after startup. This gives routes that do not have a normal request context, such as provider callbacks from a browser, a way to verify sealed credential state.

**Data flow**: It receives either a `CredentialRequests` object or `None`. It writes that value into a module-level variable. There is no returned value; the lasting effect is that later calls can retrieve or consult the installed credential request setup.

**Call relations**: This is called during server setup. Later, `installed_credential_requests` and `authorized_slot_workspace` rely on the stored value to open and check sealed authorization state.


##### `installed_credential_requests`  (lines 232–235)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or fails clearly if credential support was not configured. It gives code a single place to ask for the installed sealing setup.

**Data flow**: It reads the module-level installed value. If it is present, it returns the `CredentialRequests` object. If it is absent, it raises a runtime error explaining that no credential key is configured.

**Call relations**: This is the read side of `install_credential_requests`. Code that needs to perform credential authorization can call it instead of directly touching the module-level variable.


##### `authorized_slot_workspace`  (lines 238–254)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Figures out which workspace a provider authorization seal belongs to, but only if the seal matches the expected slot and provider payload. It returns `None` instead of raising when the seal is not usable.

**Data flow**: It receives sealed text, an expected slot, and an expected payload. It uses the installed Fernet key to open the seal, if credential requests were installed. It then checks that the seal names exactly that slot and payload. If valid, it returns the workspace ID from the seal; otherwise it returns `None`.

**Call relations**: Provider callback routes can call this when they get redirected back without a normal session. It uses `open_credential_request` underneath, but turns invalid seals into a simple “no workspace found” result.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 261–283)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Encrypts and saves a credential value for a workspace and slot. It updates an existing row if one exists, or inserts a new row if this is the first value for that slot.

**Data flow**: It receives a workspace ID, slot name, and plaintext credential. It rejects an empty value, encrypts the plaintext, opens a workspace database transaction, and tries to update the matching credential row. If no row was updated, it inserts a new encrypted row. It returns nothing, but the database now holds the encrypted credential.

**Call relations**: This is the main write path after a credential prompt or provider binding has been verified elsewhere. It uses the database transaction helper and SQL update/insert operations so callers do not have to know the storage details.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 285–297)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Fetches and decrypts a stored credential for a workspace and slot. If no value has been stored, it raises `CredentialSlotUnset` so callers can distinguish “missing” from other failures.

**Data flow**: It receives a workspace ID and slot name. It queries the credential table for encrypted bytes. If no row exists, it raises `CredentialSlotUnset`. If a row exists, it decrypts the ciphertext and returns the plaintext string.

**Call relations**: Many credential resolution paths depend on this. `slot_secret`, `slot_is_set`, and `credential_host` call it directly, and GitHub-related credential sources call it when they need stored provider bindings or tokens.

*Call graph*: called by 7 (credential_host, slot_is_set, slot_secret, bound, secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 299–330)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Safely replaces a stored credential only if it still has an expected old value. This protects token refreshes from overwriting a newer token written by another concurrent task.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects an empty replacement, reads the current encrypted value, decrypts it, and compares it to the expected value. If it matches, it writes the newly encrypted value, but only while the database row still has the same ciphertext. It returns `true` if the update succeeded and `false` if the slot was missing, different, or changed by someone else.

**Call relations**: OAuth-style clients use this after refreshing a credential outside this file. It uses the same encrypted database store as `put` and `get`, but adds a compare-before-replace step for safe concurrency.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 354–359)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host choice declaration is internally consistent. The default host must be one of the allowed hosts.

**Data flow**: After a `HostChoice` is created, it reads the default value and the allowed host list. If the default is not in the list, it raises a `ValueError`. If it is valid, nothing changes.

**Call relations**: This runs automatically when a `HostChoice` object is constructed. It protects later calls to `credential_host` and `HostChoice.resolve` from dealing with an impossible default.


##### `HostChoice.resolve`  (lines 361–366)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact host string declared by the extension. It accepts different capitalization from the stored value, but never returns a hostname outside the allowed list.

**Data flow**: It receives a selected host string. It trims surrounding spaces, lowercases it for comparison, and looks for a case-insensitive match in the declared host list. It returns the canonical declared host if found, or `None` if the selection is not allowed.

**Call relations**: `credential_host` calls this when a workspace has stored a selection for a variable provider host. This keeps proxy and sandbox behavior tied to the extension’s closed set of trusted hosts.


##### `CredentialSource.secret`  (lines 374–374)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the interface for a credential source that can mint a secret for a workspace instead of reading a user-entered secret directly. “Mint” here means create or fetch a usable short-lived credential from some provider.

**Data flow**: An implementation receives a workspace ID and the credential store. It may read stored provider bindings from the store, contact its provider, and either return a secret string or return `None` if there is nothing to mint for that workspace.

**Call relations**: `slot_secret` calls this when a slot has a provider-backed source. This function is only a protocol method here; concrete extensions provide the actual behavior.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 376–384)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the interface for asking whether a provider-backed credential source is available without actually minting a secret. This lets frequent checks avoid unnecessary provider calls.

**Data flow**: An implementation receives a workspace ID and the credential store. It checks whether the workspace has the needed binding or stored setup and returns `true` or `false`. If the binding exists but cannot be used, it may raise the same kind of failure that minting would raise.

**Call relations**: `slot_is_set` calls this when it only needs to know whether a slot should count as filled. Like `CredentialSource.secret`, this is a protocol method; provider extensions supply the real implementation.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 387–402)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the central question: what secret should this slot provide for this workspace? It prefers a provider-minted secret when a source exists, otherwise it falls back to the stored credential value.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store. If a source is present, it asks the source for a secret and returns it if one is produced. If not, it tries to read the stored value from `CredentialStore.get`. If the slot is unset, it returns `None`.

**Call relations**: This unifies credential resolution for proxy rules, sandbox exports, and other consumers. It calls `CredentialSource.secret` for provider-backed slots and `CredentialStore.get` for stored slots, so different parts of the system do not make inconsistent choices.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 405–423)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a credential slot would produce a secret, without actually producing that secret. This is useful when opening a sandbox and deciding whether to configure tools at all.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store. If a source exists and says it is bound, it returns `true`. Otherwise it tries to read the stored value. If the stored slot is missing, it returns `false`; if it exists, it returns `true`.

**Call relations**: This is the lightweight partner to `slot_secret`. It calls `CredentialSource.bound` instead of `CredentialSource.secret` to avoid provider round trips, then falls back to `CredentialStore.get` for ordinary stored credentials.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 426–443)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Determines which provider host a credential is allowed to ride to for a workspace. It returns a fixed declared host directly, or resolves a workspace’s stored choice from a closed list.

**Data flow**: It receives the credential store, workspace ID, and either a plain host string or a `HostChoice`. If given a plain string, it returns it. If given a `HostChoice`, it tries to read the workspace’s selected host from the store. If none is stored, it returns the declared default. If a value is stored, it returns the matching declared host or `None` if the stored value is not one of the allowed choices.

**Call relations**: The proxy and sandbox use this shared answer so they agree about where credentials may be sent and what host to expose. It calls `CredentialStore.get` when the host is workspace-selectable, and then relies on `HostChoice.resolve` behavior to reject undeclared hosts.

*Call graph*: calls 1 internal fn (get).


### Scoped Secret Disclosure
Provides the private sandbox callback path that releases only the specific credential needed for an authorized Git operation.

### `core/src/ufo/sandbox/proxy/credential_callback.py`

`io_transport` · `request handling`

The sandbox cache daemon sometimes needs to fetch from a private Git host, but it deliberately does not keep credentials of its own. This file is the safe “phone home” point it can call. Think of it like a locked service window inside the same building: the daemon can ask for a key for a specific workspace and Git host, but only after showing a shared bearer token, and only for the identity in the request.

The file defines `CredentialCallback`, which starts a tiny HTTP server on a private address. Each request must be a `POST` to the internal credential path, must carry the expected `Authorization: Bearer ...` header, and must stay under a small body-size limit. If the request is wrong, too large, or unauthenticated, it gets a simple JSON error.

For valid requests, the callback reads the workspace ID and Git host from JSON. It then enters that workspace context and searches the configured credential slots for a Git basic-auth credential whose stored host matches the requested host. A credential slot is a named place where a secret may be stored. If no matching secret is found, the daemon is told to proceed as public/anonymous. If a match is found, the response includes the username, token, and a workspace-scoped principal such as `w<workspace_id>`. Failed slots are logged and skipped, so one broken secret source does not leak or substitute another workspace’s identity.

#### Function details

##### `CredentialCallback.serve`  (lines 36–37)

```
async def serve(self, host: str, port: int) -> asyncio.Server
```

**Purpose**: Starts the private callback server so the cache daemon can make credential requests. Someone uses this when bringing up the proxy-side helper service.

**Data flow**: It receives a host name and port number. It passes those, plus this object’s request handler, to Python’s async server starter. The result is an `asyncio.Server`, which is the running listener ready to accept connections.

**Call relations**: This is the entry into the file’s behavior. After it asks `asyncio.start_server` to listen, every incoming connection is routed to `CredentialCallback._handle`, where the request is checked and answered.

*Call graph*: 1 external calls (start_server).


##### `CredentialCallback._handle`  (lines 39–63)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: Reads and answers one incoming HTTP request from the cache daemon. It enforces the basic safety checks before any credential lookup happens.

**Data flow**: It receives a stream reader and writer, which are the incoming bytes and outgoing bytes for one connection. It reads the request line and headers, checks that the request is a `POST` to the internal path, verifies the bearer token, checks the body size, parses the JSON body, and then asks `_resolve` for the credential answer. It writes back a JSON HTTP response and closes the connection. If the request is malformed or the connection breaks, it returns a bad-request response when possible.

**Call relations**: This function is called by the async server created in `CredentialCallback.serve`. It is the gatekeeper: only after method, path, token, and size checks pass does it call `CredentialCallback._resolve`. It uses `_respond` for every reply, whether success or error.

*Call graph*: calls 2 internal fn (_resolve, _respond); 4 external calls (readexactly, readline, close, loads).


##### `CredentialCallback._resolve`  (lines 65–79)

```
async def _resolve(self, payload: dict[str, object]) -> dict[str, object]
```

**Purpose**: Turns a validated JSON request into the credential response the daemon needs. It decides whether the request maps to a known workspace and host, and whether the answer should be anonymous or authenticated.

**Data flow**: It takes a parsed JSON-like dictionary. It reads `workspace_id` and `host`; if either is missing or not a string, it returns a public principal. If the workspace ID is not a valid UUID, it also returns public. For a valid workspace, it enters that workspace context and asks `_git_credential` for a matching Git credential. If none is found, it returns no credential and a public principal. If one is found, it returns the username, token, and a principal tied to that workspace.

**Call relations**: This function is called only after `_handle` has accepted the HTTP request as authentic. It uses the workspace context helper so credential lookup happens as that workspace, then delegates the actual slot search to `CredentialCallback._git_credential`.

*Call graph*: calls 1 internal fn (_git_credential); called by 1 (_handle); 2 external calls (ws, UUID).


##### `CredentialCallback._git_credential`  (lines 81–108)

```
async def _git_credential(self, workspace_id: UUID, host: str) -> tuple[str, str] | None
```

**Purpose**: Searches the configured credential slots for the Git username and secret that match one workspace and one Git host. It is careful to return nothing rather than borrowing a wrong or unrelated credential.

**Data flow**: It receives a workspace UUID and a host name. If no credential store exists, it returns nothing. Otherwise, it walks through the configured credential slots, skips slots that are not Git basic-user injections, checks whether the slot is set for this workspace, compares the slot’s stored host to the requested host, and reads the secret. If a usable secret is found, it returns the configured Git username and secret token. If slot lookup fails, it logs a warning and keeps looking; if nothing matches, it returns nothing.

**Call relations**: This function is called by `_resolve` after the request has been tied to a valid workspace. It relies on credential-store helpers to check whether a slot exists, read its host, and fetch its secret. It also logs successful resolutions and warns about broken slots so operators can diagnose problems without exposing the daemon to another identity.

*Call graph*: called by 1 (_resolve); 5 external calls (credential_host, slot_is_set, slot_secret, log, warn).


##### `_respond`  (lines 111–123)

```
async def _respond(writer: asyncio.StreamWriter, status: int, body: dict[str, object]) -> None
```

**Purpose**: Writes a small JSON HTTP response back to the caller. It centralizes the response format so success and error replies look the same.

**Data flow**: It receives the connection writer, an HTTP status code, and a dictionary body. It converts the body to JSON bytes, builds a minimal HTTP response with content type, content length, and connection-close headers, writes it to the stream, and waits for the bytes to flush. If the connection is already broken, it silently gives up.

**Call relations**: This helper is used by `_handle` for every outgoing answer: not found, unauthorized, too large, bad request, and successful credential resolution. It hands the final bytes to the async stream writer and does not decide what the response should mean.

*Call graph*: called by 1 (_handle); 3 external calls (drain, write, dumps).

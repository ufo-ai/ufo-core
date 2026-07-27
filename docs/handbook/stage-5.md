# Workspace identity, grants, credentials, and account connection  `stage-5`

This stage is the system’s identity checkpoint. It runs before tools act for a workspace and also supports later account-connection flows. First, workspace code decides which workspace a request belongs to, so database access, billing, and secrets stay in the right lane. Token code then creates signed proof strings: member bearer tokens, 30-day gateway tokens, short-lived artifact download tokens, and lower-level tamper-evident signing.

Next, credential code protects secrets. It encrypts stored values, checks private handoffs, shows which credential slots exist without showing the secret, and supports direct API-key connectors. Seat rules decide which members are allowed to use the agent when capacity is limited.

The connection pieces let users safely attach outside accounts. Grants record a safe account identifier, while connector objects let people list, inspect, share, or revoke connected accounts. Browser callback routes finish approval flows after an outside service sends the user back. Composio and Pipedream act as bridges to hosted connector services. Slack, GitHub, and YC-style login flows follow the same pattern: prove the user approved access, then store only the safe connection handle needed for future tool use.

## Files in this stage

### Workspace and signed identity
These files establish workspace scoping and the signed token formats used to prove member or artifact access without server-side token records.

### `core/src/ufo/workspace.py`

`orchestration` · `cross-cutting during user turns and background jobs`

This file is the project’s “workspace badge” system. Before a user turn or background job does anything sensitive, it enters `with ws(workspace_id):`. Inside that block, any code can ask `ws_current()` for the active workspace instead of passing the workspace ID through every function call.

That matters because several things must always happen in the right workspace: reading secrets, writing usage costs, and running database work under the correct row-level security scope. Row-level security means the database limits which rows can be seen or changed based on the current workspace. Without this file, code could more easily forget the workspace, use a platform-wide fallback key by accident, bill the wrong customer, or run without a clear workspace at all.

The file also centralizes credential access. A workspace may have its own stored key, often called BYOK (“bring your own key”). If it does not, the code falls back to an environment variable, which is a deployment-level default. Missing credentials fail clearly.

Billing is grouped with `billable_event()`. Code records model usage during the block, and the file writes it only if the block finishes successfully. Like a restaurant tab that is thrown away if the order fails, failed work is not billed.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that this process should use to look up workspace-specific secrets. It is meant to be called once during startup, before workspace code begins asking for credentials.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module’s shared `_store` variable. After that, credential lookups can use the store when it exists, or fall back to environment variables when it does not.

**Call relations**: Startup code calls this to prepare the workspace layer. Later, `WorkspaceScope.credential`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` rely on the saved store to decide whether workspace-owned credentials can be read or changed.


##### `BillableEvent.usage`  (lines 49–51)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one piece of metered model usage to a pending bill. Code uses it inside a billable event whenever a model call consumes tokens or otherwise creates chargeable usage.

**Data flow**: It receives a model name, a usage record, and optionally a pricing table. It stores those together in the event’s private list. Nothing is written to the database yet; this only builds up the pending bill.

**Call relations**: This is used inside the block opened by `WorkspaceScope.billable_event`. When that block exits successfully, `WorkspaceScope.billable_event` reads the accumulated entries and records them for the workspace.


##### `WorkspaceScope.credential`  (lines 60–74)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Fetches the secret value for one credential slot in the current workspace. It first tries the workspace’s own stored credential, then falls back to a deployment environment variable if no workspace-specific value is set.

**Data flow**: It takes a slot name, such as an API key label, and optionally the name of an environment variable. If a credential store is configured, it asks for this workspace’s value. If that slot is unset, it checks the environment variable named by `env`, or the upper-cased slot name. It returns the secret string, or raises `CredentialSlotUnset` if neither source has a usable value.

**Call relations**: Callers reach this through `ws_current().credential(...)`, which means a workspace must already be bound by `ws`. It calls into the configured credential store when present, and uses `CredentialSlotUnset` to make missing secrets fail loudly instead of silently continuing.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.rotate_credential`  (lines 76–81)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if the caller proves they know the expected current value. This is a compare-and-swap style update, which helps avoid overwriting a credential that changed since it was last read.

**Data flow**: It receives a slot name, the expected existing plaintext value, and the new plaintext value. If no credential store is configured, it returns `false` because there is no stored workspace credential to rotate. Otherwise it asks the store to rotate the credential for this workspace and returns whether that succeeded.

**Call relations**: This method is used through a bound `WorkspaceScope`, usually obtained from `ws_current()` or yielded by `ws`. It delegates the real encrypted storage update to the configured credential store.


##### `WorkspaceScope.put_credential`  (lines 83–87)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores an initial credential for the bound workspace. It is for cases where an authorized owner is setting up a workspace-specific secret.

**Data flow**: It receives a slot name and the plaintext secret to store. If no credential store is configured, it raises an error because there is nowhere safe to save it. Otherwise it passes the workspace ID, slot, and secret to the store.

**Call relations**: Like other credential operations, this is called from code that already has a `WorkspaceScope`. It hands the actual save operation to the configured credential store, keeping the workspace identity attached to the write.


##### `WorkspaceScope.billable_event`  (lines 90–100)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a safe billing block for the current workspace. Usage can be added while work is happening, and it is recorded only if the block finishes without an exception.

**Data flow**: It starts with an empty `BillableEvent` and yields it to the caller. The caller adds usage entries to that event. When control returns normally, it opens a workspace database transaction and records each usage entry against this workspace. If there are no entries, it does nothing. If the caller’s block raises an error, the code after the yield is skipped, so no usage is written.

**Call relations**: Code that performs chargeable work calls this through `ws_current().billable_event()`. It creates the `BillableEvent`, then uses `workspace_tx` to get a database connection and `record_workspace_usage` to write each completed charge to the workspace ledger.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 104–112)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace ID for a block of code. This lets everything inside the block know which workspace it belongs to without passing the ID through every function.

**Data flow**: It receives a workspace UUID. It stores that UUID in the shared current-workspace context and yields a `WorkspaceScope` for the same workspace. When the block ends, even if there is an error, it restores the previous context so the workspace does not leak into later work.

**Call relations**: A user turn or background job calls this at its boundary. Inside the block, `ws_current()` can retrieve the same workspace, and database transactions that read the current workspace can stay correctly scoped. It uses the database module’s current-workspace context setter and resetter to make the binding temporary.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 115–121)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises a clear error instead of letting credentialed or billable work proceed unsafely.

**Data flow**: It reads the current workspace ID from the shared context. If there is an ID, it wraps it in a `WorkspaceScope` and returns it. If there is no ID, it raises `WorkspaceUnbound` with a message explaining that the caller should use `with ws(workspace_id):`.

**Call relations**: Code throughout the system calls this when it needs workspace credentials, billing, or workspace-scoped behavior. It depends on `ws` having already set the current workspace for the surrounding turn or job.

*Call graph*: 3 external calls (__init__, __init__, get).


### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `token issuance`

This file is a small but important bridge between the control service and the shared token system. A gateway token is like a temporary signed pass: the client can store it, and later other parts of the system can check it without asking the control service again. The pass says which workspace it belongs to, which email address it was issued for, and when it expires.

The file sets two policy details in one place. First, tokens last for 30 days. Second, the secret used to sign them is expected to come from an environment variable named `UFO_TOKEN_SECRET`. An environment variable is a setting supplied outside the code, often by the server or deployment system.

The actual signing is not implemented here. Instead, this file calls the shared `ufo.bearer` token maker. That matters because the code that creates tokens and the code that verifies tokens must agree on the exact signed shape. If each side built tokens separately, a tiny mismatch could make valid users look invalid, or worse, weaken the trust check.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed gateway bearer token for one workspace and one member email address. A bearer token is a string that acts like a pass: whoever presents it can be treated as the member it was issued for, until it expires.

**Data flow**: It receives a signing secret, a workspace ID, an email address, and optionally the current time. It combines those with this file's fixed 30-day lifetime, then asks the shared bearer-token code to build the signed token. The result is a token string returned to the caller; this function does not store anything itself.

**Call relations**: When the control side needs to issue a member token, it calls this wrapper so the gateway policy is applied consistently. The function immediately hands the real token-building work to `ufo.bearer.mint_token`, which is the shared codec responsible for producing the token format that other surfaces know how to verify.

*Call graph*: 1 external calls (mint_token).


### `core/src/ufo/artifact_token.py`

`domain_logic` · `artifact sharing and artifact download request handling`

This file is a small security gate for artifact downloads. An artifact is a file the system is willing to share, but it should not be available just because someone guesses a web address. Instead, the system gives out a signed token: a compact piece of text that says which stored file may be downloaded, what filename to suggest, and when the permission expires.

The signing uses a shared secret, like a special stamp only this deployment knows. If someone changes the file key or expiry time inside the token, the stamp no longer matches and the token is rejected. If the token is too old, it is rejected too.

The file also protects the storage area. Even a correctly signed token must point inside the artifact namespace, which means its storage key must start with `artifacts/`. It also rejects keys containing `..`, a common path trick used to climb out of an allowed folder. In everyday terms, the token can open one labeled locker in the artifact room; it cannot be rewritten to open a records cabinet somewhere else.

The main pieces are `mint_artifact_token`, which makes the signed token, and `verify_artifact_token`, which turns a valid token back into trusted claims. If this file were missing or wrong, artifact links could be forged, reused forever, or aimed at files that were never meant to be downloadable.

#### Function details

##### `mint_artifact_token`  (lines 35–39)

```
def mint_artifact_token(secret: str, blob_key: str, filename: str, expires_at: int) -> str
```

**Purpose**: This function creates a signed download token for one artifact file. It is used when the system wants to give someone temporary permission to download a shared file.

**Data flow**: It receives the deployment secret, the stored artifact key, the suggested download filename, and the expiry time. It first refuses to work if there is no secret, because an unsigned or weakly signed token would not protect anything. Then it packs the key, filename, and expiry into JSON text and asks the token-signing helper to seal that text with the secret. The result is a token string that can later be sent to a downloader.

**Call relations**: This is the token-making side of the flow. The artifact-sharing path calls on it when creating a link, and it hands the prepared JSON payload to `ufo.token_signing.sign_token` so the payload can be protected against tampering. The matching checker is `verify_artifact_token`, which expects the same fields and the same secret.

*Call graph*: 3 external calls (__init__, dumps, sign_token).


##### `verify_artifact_token`  (lines 42–63)

```
def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims
```

**Purpose**: This function checks whether a download token is trustworthy and still valid. If everything is acceptable, it returns the exact artifact key, filename, and expiry time that the download route may use.

**Data flow**: It receives a token, the deployment secret, and the current time. It refuses to continue if the secret is missing. It then asks the token-signing helper to verify the token’s signature and recover the original JSON payload. If the signature is bad, the JSON is broken, or the payload is not a plain object, it raises an artifact token error. Next it builds `ArtifactClaims` from the payload, checks that the blob key stays under `artifacts/` and does not contain `..`, and checks that the expiry time is still in the future. If all checks pass, it returns the trusted claims; otherwise it raises an error and no file should be served.

**Call relations**: This is the token-checking side of the flow. The artifact download route calls on it before reading or returning any bytes. It relies on `ufo.token_signing.verify_token` to prove the token was made with the shared secret, then performs artifact-specific safety checks before handing back `ArtifactClaims` to the caller.

*Call graph*: 6 external calls (__init__, __init__, timestamp, loads, PurePosixPath, verify_token).


### `core/src/ufo/bearer.py`

`domain_logic` · `token minting and request authentication`

This file is the shared rulebook for UFO's member authentication token. A bearer token is like a signed wristband: whoever presents it can be recognized, but only if the signature proves UFO issued it and the expiry time has not passed.

The token contains three pieces of information: the workspace id, the member email, and an expiration time. That information is turned into compact JSON, encoded safely for use in a string, and signed with HMAC-SHA256. HMAC is a way to make a tamper-evident stamp using a shared secret. If anyone changes the workspace, email, or expiry, the signature no longer matches.

There is no database lookup here. The token is self-contained. That matters because hosted gateways, local developer setup, terminal extensions, and debug surfaces can all agree on one token shape. The only shared ingredient is the secret stored in the UFO_TOKEN_SECRET environment variable.

The file has two main sides. `mint_token` creates a signed token. `verified_claims` checks the signature, decodes the token, confirms it has the expected fields, and rejects expired tokens. `verify_token` adds a workspace match for deployments pinned to one workspace. `workspace_claim` extracts the workspace for shared services that serve many workspaces.

#### Function details

##### `mint_token`  (lines 28–45)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a new bearer token for a workspace and member email. It signs the token so later code can detect any tampering.

**Data flow**: It receives a signing secret, workspace id, email address, time-to-live, and optionally a clock time. It trims and lowercases the email, calculates the expiry time, packs the claims into JSON, encodes that JSON into a URL-safe string, signs that string with the secret, and returns one combined token string containing the encoded body and signature.

**Call relations**: This is the issuing side of the codec. Other token minters use it so all issued tokens have the same shape that `verified_claims` later expects when requests present those tokens.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 48–71)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the workspace id and email it proves. If anything looks wrong, it returns nothing instead of trusting the token.

**Data flow**: It receives a token string and optionally the current time as a Unix timestamp. It reads the shared secret from the environment, splits the token into body and signature, recomputes the expected signature, compares signatures in a timing-safe way, decodes the body as JSON, checks that workspace, email, and expiry have the right types, rejects expired tokens, and returns the workspace and email as a pair.

**Call relations**: This is the central verification step. `verify_token` calls it before checking that the token belongs to a specific workspace, and `workspace_claim` calls it before turning the workspace claim into a UUID. It relies on `_secret` for the signing key and `_b64url_decode` to unpack the token body.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 74–85)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Authenticates a token for one specific workspace. It returns the member email only if the token is valid and its workspace claim matches the workspace being checked.

**Data flow**: It receives a token, a workspace UUID, and optionally the current time. It asks `verified_claims` to prove the token first; if verification fails, it returns nothing. If the signed workspace claim does not equal the given workspace id, it also returns nothing. Otherwise it returns the member email in lowercase.

**Call relations**: This is used when a process is tied to one workspace and must reject tokens minted for any other workspace. It builds directly on `verified_claims`, adding the tenant-specific safety check after the general signature and expiry checks are done.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 88–99)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Finds the workspace named by a valid bearer token. This is useful for shared services that serve many workspaces and must decide the workspace from each request.

**Data flow**: It receives a token and optionally the current time. It first uses `verified_claims` to make sure the token is authentic and unexpired. Then it tries to convert the signed workspace string into a UUID. It returns that UUID if successful, or nothing if verification fails or the workspace value is not a valid UUID.

**Call relations**: This is the shared-fleet version of token checking. Instead of comparing against one fixed workspace like `verify_token`, it trusts the already-verified workspace claim and turns it into the standard UUID form.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 102–106)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the environment. It stops verification immediately if the secret is missing, because tokens cannot be safely checked without it.

**Data flow**: It looks for the `UFO_TOKEN_SECRET` environment variable. If the value exists, it returns it. If it is empty or missing, it raises an error explaining that the secret must be set.

**Call relations**: This helper is used by `verified_claims` whenever a token needs to be checked. Keeping the read here means callers can hand over tokens for verification without directly handling the secret themselves.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 109–110)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the compact URL-safe token body back into bytes. It also restores any missing padding characters that were stripped when the token was created.

**Data flow**: It receives the encoded body string. It adds the right number of `=` padding characters, decodes the URL-safe base64 text, and returns the original bytes so JSON parsing can read them.

**Call relations**: This is a small decoding helper used inside `verified_claims` after the token signature has matched. It reverses the encoding done by `mint_token`.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file solves a common trust problem: sometimes the system needs to hand a client or another component a small piece of data, then later get it back and know it was not altered. It does that by making a signed token. Think of it like putting a note in an envelope and sealing it with a wax stamp: anyone can carry the envelope, but if the seal no longer matches, the system rejects it.

The payload is first turned into base64url text, which is a web-safe way to represent raw bytes using ordinary characters. Then the file computes an HMAC, which is a secret-key fingerprint: only someone with the shared secret can produce the correct fingerprint for that exact token body. The final token is two text parts separated by a dot: the encoded payload and its signature.

When checking a token, the file splits it, recalculates what the signature should be, and compares the two using a timing-safe comparison. That matters because ordinary string comparison can leak tiny timing clues. If the token is missing pieces, has the wrong signature, or contains unreadable payload text, it raises SignedTokenError instead of returning bad data.

#### Function details

##### `sign_token`  (lines 12–16)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: This function turns raw payload bytes into a text token that can later be checked for tampering. A caller uses it when it wants to store or send a small piece of data outside the trusted server while still being able to verify it later.

**Data flow**: It receives a secret key and the payload bytes. It converts the payload into web-safe base64 text, computes a SHA-256 HMAC signature over that text using the secret, converts the signature into web-safe base64 text too, and returns both parts joined with a dot. It does not change anything outside itself.

**Call relations**: This is the token-making side of the pair. It relies on the standard base64 encoder to make bytes safe for text transport, and on HMAC creation to produce the secret fingerprint that verify_token will later recalculate and check.

*Call graph*: 2 external calls (urlsafe_b64encode, new).


##### `verify_token`  (lines 19–30)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: This function checks that a token has the right shape, has not been tampered with, and contains readable payload bytes. A caller uses it before trusting any data that came back in signed-token form.

**Data flow**: It receives a token string and the same secret key used to sign it. It splits the token into payload text and signature text, rebuilds the expected signature from the payload text, compares that expected signature with the supplied one, and finally decodes the payload back into bytes. If any step fails, it raises SignedTokenError instead of returning untrusted data.

**Call relations**: This is the token-checking side of the pair. It mirrors sign_token by using the same HMAC and base64 steps, then uses a safe comparison routine to avoid leaking signature clues. If the token is malformed, mismatched, or unreadable, it stops the flow by raising SignedTokenError.

*Call graph*: 5 external calls (__init__, b64decode, urlsafe_b64encode, compare_digest, new).


### Credential catalog and secret access
These files expose credential slots safely, protect stored secrets, and let trusted host-side work retrieve only the credentials it is authorized to use.

### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling`

Some extensions need a user-supplied secret, such as an API key. This file presents those needed secrets as “credential” objects in the workspace. The important idea is that the object is the slot, not the secret. A slot can be filled or empty, but reads never return the value, and they do not even return a fingerprint of the value.

The file starts with small data shapes for declared slots and for the public information shown about a slot. A declared slot says what the extension asked for, what it is called, and where the credential may be injected. The visible specification, `CredentialSpec`, contains only safe description data such as the slot name, extension, and host choices.

`CredentialObjects` is the main object-kind implementation. It can list all declared slots, fetch one slot’s safe details, report whether a slot is filled, and delete a stored value. Creating or updating a credential object is deliberately refused, because filling a secret needs a special private handoff called `request_credentials`. Think of this like a locked mailbox: this file can tell you the mailbox exists and whether something is inside, but it will not open the envelope. Deleting is also protected: only the workspace owner may clear a filled slot.

#### Function details

##### `_slug`  (lines 73–74)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a raw credential slot name into a simple, URL-like object name. This gives the system stable, readable names such as `github-token` instead of names with spaces or punctuation.

**Data flow**: It receives a text name, lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens from the ends, and returns the cleaned name.

**Call relations**: When `CredentialObjects._named` is building the public names for all credential slots, it calls this helper first. If two slots clean up to the same name, `_named` adds an extra short hash afterward.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `CredentialObjects.list`  (lines 86–99)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows all declared credential slots as workspace object rows. Each row says which extension declared the slot, gives a short description, and says whether the slot is currently filled or empty.

**Data flow**: It receives the tool context and a list query. It builds the public name-to-slot map, asks the database which slots have stored credential rows, creates one visible row per declared slot, and returns a paged result using the query’s paging rules.

**Call relations**: This is used when something asks to browse credential objects. It depends on `_named` to decide each slot’s visible object name, `_filled_slots` to know which slots have stored values, and then hands the finished rows to the shared object paging helper.

*Call graph*: calls 2 internal fn (_filled_slots, _named); 2 external calls (__init__, object_page).


##### `CredentialObjects.get`  (lines 101–125)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the safe details for one credential slot, if that slot exists. It includes the declaration and timestamps for when a value was stored, but never includes the credential value itself.

**Data flow**: It receives a context and an object name. It looks up the declared slot by that public name, reads the current workspace’s credential table for matching timestamps, and returns an object detail containing a `CredentialSpec`; if no slot matches the name, it returns nothing.

**Call relations**: This is used when a caller opens one credential object. It first relies on `_named` to translate the object name back to the declared slot, then reads only safe timestamp fields from the workspace database and wraps them in the standard object-detail shape.

*Call graph*: calls 1 internal fn (_named); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects.status`  (lines 127–145)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live state of one credential slot in a small status dictionary. At minimum it says whether the slot is filled, and when host information applies it may also report the resolved host.

**Data flow**: It receives a context and an object name. It finds the declared slot, checks whether a credential row exists for the current workspace and slot, builds `{"filled": true}` or `{"filled": false}`, and may add a host value by asking the credential helper to resolve it.

**Call relations**: This is used when the object system wants current status rather than the full declaration. It uses `_named` to find the slot, checks the workspace database directly, and delegates host resolution to `credential_host` when the slot has host-related rules and a credential store is available.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 147–150)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None) -> None
```

**Purpose**: Deliberately refuses create or update operations for credential objects. This protects secrets by forcing filling and rotation to happen through the dedicated `request_credentials` flow instead of ordinary object editing.

**Data flow**: It receives the context, object name, requested spec, and any old spec, but does not use them to change anything. It immediately raises a “verb not supported” error with an explanation of the safer path.

**Call relations**: This function is called by the object system when someone tries to create or update a credential object. Rather than handing off to storage, it stops the flow and points the caller toward the private credential request process.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 152–162)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Clears the stored value for a credential slot while leaving the slot itself declared and visible. Only the workspace owner is allowed to do this.

**Data flow**: It receives the context and object name. It first asks whether the current speaker is the workspace owner; if not, it raises an owner-required error. If allowed, it finds the matching declared slot and deletes that slot’s credential row for the current workspace from the database.

**Call relations**: This is used when someone deletes a credential object. It does not remove the extension’s declaration, so the slot will still appear afterward as empty. It relies on the tool context for the owner check, `_named` to identify the slot, and the workspace database transaction to remove the stored credential row.

*Call graph*: calls 2 internal fn (_named, speaker_is_owner); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 164–176)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Builds the map from public object names to declared credential slots. It also prevents name collisions when two slots would otherwise get the same cleaned-up name.

**Data flow**: It reads the object’s tuple of declared slots. For each slot it creates a plain slug name; if only one slot has that slug, it uses it directly. If several slots collide, it adds a short hash based on the extension and original slot name, then returns the finished name-to-slot dictionary.

**Call relations**: This is the shared naming step used by list, get, status, and delete. It calls `_slug` for readable base names and uses a hash only when needed, so most names stay friendly while duplicate names remain unambiguous.

*Call graph*: calls 1 internal fn (_slug); called by 4 (delete, get, list, status); 1 external calls (sha256).


##### `CredentialObjects._filled_slots`  (lines 178–187)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the active workspace. It returns only slot names, not the secret values.

**Data flow**: It opens a workspace database transaction, selects the slot column from credential rows for the current workspace, and returns those slot names as a frozen set.

**Call relations**: This helper supports `CredentialObjects.list`. The list view uses it to add the human-friendly “filled” or “empty” label beside each declared slot without reading any encrypted credential data.

*Call graph*: called by 1 (list); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting: startup setup, credential fulfillment, provider callbacks, sandbox/proxy request preparation`

This file is the project's safe deposit box for credentials. A credential is a secret value, such as an API token, that an outside service needs. The code makes sure those secrets are encrypted before they are written to the database, and only decrypted at the last moment when the proxy needs to inject the real value.

It also protects the path where a user supplies a secret. Instead of putting the secret in chat, the system creates a short-lived sealed request. Think of it like a signed claim ticket: it says which workspace, which member, and which credential slot are allowed. When the private prompt or provider callback comes back, the ticket is opened and checked before anything is stored.

Some credentials are not typed by a user at all. They are minted from a provider, such as a short-lived token created from an installation. This file defines the shared interface for that kind of source, plus helper functions that answer, in one consistent way, whether a slot has a secret and what that secret is.

It also supports provider hosts that vary by account. Those hosts must come from a declared closed list, not free text, because the proxy may trust the answer when deciding where network traffic may go.

#### Function details

##### `seal_credential_request`  (lines 61–62)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Turns a credential request state into an encrypted, opaque string. Callers use it when they need to give another part of the system a safe claim ticket that cannot be read or altered by the user.

**Data flow**: It receives a Fernet encryption helper and a structured state object. It converts the state to JSON text, encrypts that text, and returns the encrypted result as a normal string that can travel through a browser or prompt.

**Call relations**: This is the shared sealing step used by CredentialRequests.seal, CredentialRequests.authorize, and seal_installation. Those higher-level functions decide what the ticket means; this function only locks the ticket closed.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 65–91)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Opens and verifies an encrypted credential ticket. It makes sure the ticket was created by this deployment, has not expired when a time limit applies, has the expected shape, and was sealed for the expected purpose.

**Data flow**: It receives a Fernet helper, an encrypted string, the expected purpose, and optionally a time-to-live limit. It decrypts the string, parses it into a CredentialRequestState, checks the purpose, and returns the trusted state; if anything is wrong, it raises CredentialRequestInvalid.

**Call relations**: CredentialRequests.open_authorization, authorized_slot_workspace, and open_installation call this before trusting any sealed value. It centralizes the safety checks so each caller does not have to remember how to reject forged, expired, or wrong-purpose tickets.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 103–110)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request saying that a particular member may fill specific credential slots for a workspace. It refuses slots that no installed extension declared.

**Data flow**: It receives a workspace ID, member ID, and slot names. It checks every slot against the known declared slots, builds a request state, seals it, and returns the encrypted ticket string.

**Call relations**: This is used when the system asks a member to provide credentials privately. It hands off to seal_credential_request after checking that the request only names legitimate slots.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 112–125)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization ticket for a provider-based credential flow. It records one slot and provider state so a later callback can prove it belongs to the same member, workspace, and slot.

**Data flow**: It receives a workspace ID, member ID, slot name, and provider payload. It rejects unknown slots and empty payloads, packages the information into a request state, encrypts it, and returns the sealed string.

**Call relations**: This is part of starting an external authorization flow, such as sending a user to a provider. It uses seal_credential_request to produce the protected state that will later be checked by CredentialRequests.open_authorization or authorized_slot_workspace.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 127–141)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a sealed provider authorization for exactly one expected workspace, member, and credential slot. If it checks out, it returns the provider state stored inside.

**Data flow**: It receives a sealed string plus the workspace, member, and slot that the caller expects. It opens the sealed state, compares each claim with the expected values, verifies the slot is declared, and returns the payload; otherwise it raises an error.

**Call relations**: This is the matching read side for authorization tickets created by CredentialRequests.authorize. It relies on open_credential_request for basic seal validation, then adds the more specific checks needed before storing or exchanging provider credentials.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 144–158)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Stores a provider installation ID as a sealed workspace binding instead of as bare text. This prevents one workspace from typing or guessing another organization's installation ID and using it.

**Data flow**: It receives a Fernet helper, workspace ID, slot name, and installation ID. It builds a special-purpose state object, puts the installation ID in the payload, encrypts it, and returns the sealed binding string.

**Call relations**: This function uses the same lower-level sealing helper as member credential requests, but marks the state with an installation-specific purpose. open_installation later opens and checks this binding.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 161–172)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed provider installation binding and confirms it belongs to the expected workspace and slot. It rejects forged values, wrong workspaces, wrong slots, wrong purposes, and unsealed IDs.

**Data flow**: It receives a Fernet helper, workspace ID, slot name, and sealed binding. It opens the binding without an expiry time, checks the workspace and slot, and returns the installation ID payload if valid.

**Call relations**: This is the safe counterpart to seal_installation. It calls open_credential_request for the common decrypt-and-validate step, then enforces the installation-specific ownership checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 178–184)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Installs the process-wide credential request authority at server startup. This gives routes that do not have a normal request context, such as browser provider callbacks, a way to open sealed credential state.

**Data flow**: It receives either a CredentialRequests object or None. It stores that value in a module-level variable so later code can find the configured encryption helper and declared slots.

**Call relations**: Startup code calls this once to publish credential-request support for the running process. Later, installed_credential_requests and authorized_slot_workspace read the installed value.


##### `installed_credential_requests`  (lines 187–190)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or fails clearly if credential support was not configured. Callers use it when they need the configured sealing and opening tools.

**Data flow**: It reads the module-level installed credential request object. If one exists, it returns it; if not, it raises a runtime error explaining that no credential key is configured.

**Call relations**: This is the accessor for the value set by install_credential_requests. It protects later credential flows from silently running without the encryption key they need.


##### `authorized_slot_workspace`  (lines 193–209)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Figures out which workspace a provider authorization callback belongs to, using only the sealed callback state. It returns None instead of raising when the callback state is not valid for the expected slot and payload.

**Data flow**: It receives a sealed string, slot name, and provider payload. It opens the sealed state using the installed credential request authority, checks that the state names exactly that slot and payload, and returns the workspace ID if all checks pass; otherwise it returns None.

**Call relations**: Provider callback routes use this when a browser returns without a normal session or turn context. It relies on open_credential_request and the value installed by install_credential_requests to safely recover the workspace.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 216–238)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Encrypts and saves a credential value for a workspace and slot. It updates an existing row if one exists, or inserts a new row if this is the first value for that slot.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty secret, encrypts the text, opens a database transaction, tries to update the existing credential row, and inserts a row if nothing was updated.

**Call relations**: Credential fulfillment code uses this after a private handoff or provider flow has produced a real secret. It is the write side paired with CredentialStore.get and CredentialStore.rotate.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 240–252)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads and decrypts the stored credential for one workspace and slot. It raises a specific missing-slot error when no credential has been stored.

**Data flow**: It receives a workspace ID and slot name. It queries the credential table for encrypted bytes, raises CredentialSlotUnset if there is no row, decrypts the ciphertext if present, and returns the plaintext string.

**Call relations**: This is the common read path used by credential_host, slot_is_set, slot_secret, and GitHub app token code. Higher-level helpers call it when they need the stored member-provided value.

*Call graph*: called by 5 (credential_host, slot_is_set, slot_secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 254–285)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still contains an expected old value. This is useful when refreshing tokens, because it avoids overwriting a newer token written by another concurrent refresh.

**Data flow**: It receives a workspace ID, slot name, expected current value, and new plaintext value. It rejects an empty new value, reads and decrypts the current row, compares it with the expected value, and only then writes encrypted replacement text; it returns true if the replacement happened.

**Call relations**: OAuth-style token refresh code can use this after getting a new token from a provider. It shares the same database and encryption approach as put and get, but adds a compare-before-write safety check.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 309–314)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a HostChoice declaration is internally consistent. The default host must be one of the declared allowed hosts.

**Data flow**: After a HostChoice object is created, it reads the default host and the allowed host list. If the default is missing from the list, it raises a ValueError; otherwise the object remains valid.

**Call relations**: This runs automatically when a HostChoice is constructed. It protects later calls to credential_host and HostChoice.resolve from dealing with an impossible default.


##### `HostChoice.resolve`  (lines 316–321)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string, or rejects it by returning None. It compares case-insensitively because internet host names are not case-sensitive.

**Data flow**: It receives the stored selection text. It trims surrounding spaces, lowercases it for comparison, searches the declared host list, and returns the matching declared host spelling if found; otherwise it returns None.

**Call relations**: credential_host calls this when a workspace has stored a choice for a variable provider host. The returned value is safe because it always comes from the declaration, not directly from user text.


##### `CredentialSource.secret`  (lines 329–329)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the required method for a credential source that can mint a secret on demand. A source uses this when the real credential should be created from a provider instead of stored directly by the member.

**Data flow**: An implementation receives a workspace ID and CredentialStore. It may read stored binding information, contact its provider, and return a freshly minted secret, or return None if this workspace cannot mint one.

**Call relations**: slot_secret calls this first when a slot has a source. Concrete provider code, such as an extension, supplies the actual behavior behind this protocol method.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 331–335)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the required method for checking whether a workspace has enough provider binding to mint a secret, without actually minting one. This avoids expensive or unnecessary provider calls.

**Data flow**: An implementation receives a workspace ID and CredentialStore. It checks local stored information or other cheap state and returns true if minting should be possible, false otherwise.

**Call relations**: slot_is_set calls this when it only needs to know whether a credential slot is available. Provider implementations supply the real check.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 338–353)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the main question: what secret should this workspace use for this credential slot? It prefers a provider-minted secret when a source exists, and otherwise falls back to the stored encrypted value.

**Data flow**: It receives the slot name, optional credential source, workspace ID, and store. If there is a source, it asks the source for a minted secret and returns it if present; if not, it tries to read the stored value and returns that, or None if the slot is unset.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers should go through this helper so they all see the same answer. It calls CredentialSource.secret and CredentialStore.get rather than letting each caller invent its own lookup order.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 356–368)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a credential slot would produce a secret, without producing or minting the secret. This lets the system decide whether to configure a client without making a provider round trip.

**Data flow**: It receives the slot name, optional source, workspace ID, and store. It first asks the source whether the workspace is bound; if not, it tries to read the stored credential and returns true if found, false if missing.

**Call relations**: Sandbox setup can call this on every open to decide whether a credential-dependent client should be configured. It calls CredentialSource.bound for provider-backed slots and CredentialStore.get for stored slots.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 371–388)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Resolves the host that a credential is allowed to be used with for one workspace. It returns a fixed declared host directly, or a safe declared choice selected by the workspace.

**Data flow**: It receives a credential store, workspace ID, and either a plain host string or a HostChoice. For a plain string, it returns that string. For a HostChoice, it reads the workspace's stored selection, falls back to the default if nothing is stored, and returns the matching declared host or None if the stored choice is invalid.

**Call relations**: Both the egress proxy and sandbox environment setup use this so they agree on the exact host tied to a credential. It calls CredentialStore.get for stored host choices and HostChoice.resolve to ensure the final host comes from the allowed list.

*Call graph*: calls 1 internal fn (get).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `during source sync authentication`

Some source connectors need to talk to an outside provider using an API key that a workspace member supplied directly. This file is the small bridge that retrieves that saved secret at the moment a sync job needs it. The important safety rule is that the secret stays on the host side: it is read from the protected credential store, used to authenticate outgoing provider requests, and is not passed into a sandbox or exposed to an agent.

The main piece is `DirectAuthProxy`, a frozen data class, meaning it is a simple object whose stored fields should not change after creation. It holds a `CredentialAccess` object, which is the project’s controlled doorway into stored credentials. When asked for credentials, it looks up the credential slot named after the connector’s provider. It then wraps the secret as a `Credential` with a bearer token, which is the common “send this token in the Authorization header” style of web authentication.

In everyday terms, this file is like a locked key cabinet attendant: given the provider name, it retrieves only the matching key and hands it to the trusted server-side worker, not to everyone in the building.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method retrieves the saved API key for a provider and turns it into a credential object that provider HTTP requests can use. It is used when the source is routed through the direct-account path, where the API key itself is the account proof.

**Data flow**: It receives a workspace identifier, a provider name, and an account handle. The provider name is used to read the matching secret from the workspace-scoped credential store through `self.credentials`; the account handle is not used because direct authentication is based on the stored provider key. The method returns a `Credential` whose bearer token is the retrieved secret.

**Call relations**: When the sync flow needs authentication for a directly keyed source, this method is the point where the stored secret is converted into the standard credential shape used by the rest of the system. After reading the secret, it creates a `Credential` object so downstream provider-request code can authenticate without needing to know how the key was stored.

*Call graph*: 1 external calls (__init__).


### Workspace membership seats
This file defines the shared policy for which workspace members are allowed to receive agent service when seat limits apply.

### `core/src/ufo/seats.py`

`domain_logic` · `cross-cutting: member creation, admission checks, conversation enforcement, and seat administration`

A “seat” is permission for a workspace member to use the agent. Some workspaces are unlimited, so everyone can talk. Others have a seat limit, and possibly an included allowance that can be filled automatically. This file keeps those rules in one place so admission checks, running conversations, onboarding, and billing-related tools all make the same decision.

The main idea is simple: a member record may exist even if that person is not seated. That lets the system remember who they are, but the agent may refuse to answer until the workspace owner grants a seat. Think of it like a building lobby: a person can be known to reception, but still needs a badge to enter.

The `Seats` class is the main interface for one workspace. It can check whether seating applies, decide whether a member is admitted, create a snapshot for display, grant or revoke seats, and automatically seat new members if space is available. It uses database locks when counting and changing seats, so two requests cannot accidentally hand out the same last seat. The file also defines the special rule that the first-created member is the owner, and the owner’s seat cannot be revoked because that could leave nobody able to grant seats again.

#### Function details

##### `gate_member`  (lines 45–59)

```
def gate_member(speaker_member_id: UUID | None, admission_source: TurnAdmissionSource, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. Usually this is the speaker, but scheduled work is checked against the member it is acting for.

**Data flow**: It receives the visible speaker member ID, the source of the turn, and any member the turn is acting on behalf of. It returns the speaker if there is one; otherwise, for scheduled turns, it returns the creator/on-behalf-of member; otherwise it returns nothing, meaning no member needs seat checking.

**Call relations**: This is the shared rule used wherever the system needs to decide whose seat matters. It prevents different parts of the system from disagreeing about whether a scheduled or internal turn should be allowed.


##### `seat_gate_absent`  (lines 62–68)

```
def seat_gate_absent(workspace_id: UUID) -> bool
```

**Purpose**: Quickly answers whether a workspace was recently found to have no seat limits. This lets common unlimited workspaces skip an extra database check for a short time.

**Data flow**: It receives a workspace ID and looks in a small in-memory cache for an expiry time. If the cached entry exists and has not expired, it returns true; otherwise it returns false.

**Call relations**: It is a fast path used by seat enforcement code outside this file. The cache entries it reads are written by `_note_absent_limit` when `Seats.gated` or `Seats.admits` discovers that a workspace has no seating limits.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_limit`  (lines 71–76)

```
def _note_absent_limit(workspace_id: UUID) -> None
```

**Purpose**: Records that a workspace currently has no seat limit or included-seat setting. This supports the short-lived fast path for unlimited workspaces.

**Data flow**: It receives a workspace ID, checks the current clock, clears expired cache entries if the cache is full, and stores a new expiry time a few seconds in the future. It does not return a value; it changes the module-level cache.

**Call relations**: `Seats.gated` and `Seats.admits` call this after reading the database and finding that both seating controls are absent. `seat_gate_absent` later reads the cached result to avoid repeating that database work immediately.

*Call graph*: called by 2 (admits, gated); 1 external calls (monotonic).


##### `SeatSnapshot.seated`  (lines 106–107)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a seat snapshot currently have seats. This is useful for displaying or checking current seat usage.

**Data flow**: It reads the snapshot’s list of member entries, counts the entries marked as seated, and returns that number. It does not change the snapshot.

**Call relations**: This property belongs to the read-only `SeatSnapshot` produced by `Seats.snapshot`. Code that receives a snapshot can use it to show seat usage without re-counting members itself.


##### `Seats.gated`  (lines 118–132)

```
async def gated(self, connection: AsyncConnection) -> bool
```

**Purpose**: Checks whether a workspace enforces seating at all. If neither a hard seat limit nor an included-seat allowance is set, the workspace is treated as unlimited.

**Data flow**: It receives a database connection, reads the workspace’s `seat_limit` and `included_seats` fields, and returns false when both are empty. In that unlimited case it also records a short cache note; otherwise it returns true.

**Call relations**: This is called when code needs to know whether seat rules apply before doing stricter checks. When it finds no limit, it hands that fact to `_note_absent_limit` so later enforcement can skip a database round trip briefly.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.admits`  (lines 134–161)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Decides whether a specific member is allowed to be answered by the agent in this workspace. Unlimited workspaces admit everyone who has a matching member record; limited workspaces admit only seated members.

**Data flow**: It receives a database connection and member ID. It joins the member to the workspace, checks that the member belongs to this workspace, reads the workspace’s seat settings, and returns true or false. If the workspace is unlimited, it also updates the short-lived no-limit cache.

**Call relations**: Admission and per-round enforcement can call this when deciding whether a member’s turn may continue. It uses `_note_absent_limit` for the unlimited fast path and otherwise relies directly on the member’s `seated_at` value.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 163–185)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of the workspace’s seat state. This is the data a tool or UI would need to show the limit, included allowance, members, and who is seated.

**Data flow**: It receives a database connection, reads the workspace’s seat bounds, then reads all members ordered by creation time. It turns those rows into `SeatEntry` objects, marking the first member as the owner, and returns a `SeatSnapshot`.

**Call relations**: This function is the reporting side of the seat system. Unlike `grant`, `revoke`, or `auto_seat`, it does not change anything; it packages the current database state for callers that need to inspect it.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 187–199)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Gives a seat to the member with the given email address. It is safe to call again for someone already seated, and it refuses to exceed the workspace’s hard limit.

**Data flow**: It receives a database connection and email address. It locks the workspace’s seat settings, finds the member by email, returns immediately if they already have a seat, counts currently seated members if there is a limit, and either raises `SeatLimitReached` or writes the seat timestamp.

**Call relations**: This is the explicit owner-grant path. It depends on `_locked_limits` to avoid races, `_member_by_email` to find the target, `_seated_count` to check capacity, and `_seat` to perform the final update.

*Call graph*: calls 4 internal fn (_locked_limits, _member_by_email, _seat, _seated_count); 1 external calls (__init__).


##### `Seats.revoke`  (lines 201–215)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a seat from the member with the given email address. It refuses to revoke the workspace owner’s seat so the workspace cannot get stuck with no one able to grant seats.

**Data flow**: It receives a database connection and email address. It locks the workspace settings, finds the member, checks whether that member is the owner, and if the member is seated it clears their `seated_at` value. If they are already unseated, it does nothing.

**Call relations**: This is the explicit unseat path. It uses `_locked_limits` and `_member_by_email`, asks `owner_member_id` who the owner is, and then updates the member row if revocation is allowed.

*Call graph*: calls 3 internal fn (_locked_limits, _member_by_email, owner_member_id); 3 external calls (__init__, execute, update).


##### `Seats.ensure_limit`  (lines 217–229)

```
async def ensure_limit(self, connection: AsyncConnection, limit: int) -> None
```

**Purpose**: Sets the workspace’s hard seat limit only if it has not been set before. This protects an existing operator- or billing-chosen value from being overwritten by a repeated setup step.

**Data flow**: It receives a database connection and a limit number. It rejects values below 1, then updates the workspace only where the current `seat_limit` is still empty. It returns nothing and may leave the row unchanged if a limit already exists.

**Call relations**: This is used by setup or billing-extension code that wants to establish a limit once. It does not call the granting logic; it only writes the workspace-level boundary that later functions such as `grant` and `auto_seat` obey.

*Call graph*: 2 external calls (execute, update).


##### `Seats.ensure_included`  (lines 231–243)

```
async def ensure_included(self, connection: AsyncConnection, included: int) -> None
```

**Purpose**: Sets the number of seats that may be filled automatically, but only if that value has not already been set. This represents the plan’s included allowance.

**Data flow**: It receives a database connection and an included-seat count. It rejects values below 1, then updates the workspace only when `included_seats` is currently empty. It returns nothing.

**Call relations**: This is another one-time establishment helper for setup or billing flows. The value it writes is later used by `auto_seat` to decide how many new members can be silently seated before owner approval is needed.

*Call graph*: 2 external calls (execute, update).


##### `Seats.auto_seat`  (lines 245–254)

```
async def auto_seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Automatically seats a newly created member if the workspace still has room within the silent allowance. If the allowance is full, the member still exists but remains unseated.

**Data flow**: It receives a database connection and member ID. It locks the workspace limits, chooses the included-seat allowance if present or the hard limit otherwise, counts currently seated members when there is a bound, and either returns without change or writes a seat timestamp for the member.

**Call relations**: This is part of member creation. `create_member` creates the member row and then uses this rule so every new-member path applies the same automatic seating behavior.

*Call graph*: calls 3 internal fn (_locked_limits, _seat, _seated_count).


##### `Seats._locked_limits`  (lines 256–264)

```
async def _locked_limits(self, connection: AsyncConnection) -> tuple[int | None, int | None]
```

**Purpose**: Reads the workspace’s seat settings while locking the workspace row. The lock is a database guard that stops two concurrent operations from making conflicting seat-count decisions.

**Data flow**: It receives a database connection, selects `seat_limit` and `included_seats` for this workspace with a row lock, and returns both values. It does not itself decide anything about seating.

**Call relations**: `grant`, `revoke`, and `auto_seat` call this before changing or counting seats. It is the shared safety step that keeps seat updates from racing each other.

*Call graph*: called by 3 (auto_seat, grant, revoke); 2 external calls (execute, select).


##### `Seats._member_by_email`  (lines 266–279)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None]
```

**Purpose**: Finds a workspace member by email address for grant and revoke operations. It normalizes the lookup so capitalization and extra spaces do not cause accidental misses.

**Data flow**: It receives a database connection and email address, trims and lowercases the email for comparison, and reads the member ID plus current seat timestamp. It returns those two values, or raises `UnknownMember` if no matching member exists.

**Call relations**: `Seats.grant` and `Seats.revoke` call this after locking limits. It gives those higher-level operations the exact member row they should update.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_count`  (lines 281–289)

```
async def _seated_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many members in this workspace currently have seats. This is used before handing out another seat when a limit or allowance may be full.

**Data flow**: It receives a database connection, counts member rows in the workspace whose `seated_at` field is not empty, and returns the count as an integer.

**Call relations**: `Seats.grant` calls this to enforce the hard seat limit. `Seats.auto_seat` calls it to decide whether the automatic included allowance has already been used up.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, select).


##### `Seats._seat`  (lines 291–296)

```
async def _seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Marks a member as seated by writing the current time into their member row. This is the small shared write used by both manual grants and automatic seating.

**Data flow**: It receives a database connection and member ID, updates that member’s `seated_at` and `updated_at` timestamps to now, and returns nothing.

**Call relations**: `Seats.grant` calls this after confirming the member may receive a seat. `Seats.auto_seat` calls it when a newly created member fits within the automatic allowance.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, update).


##### `owner_member_id`  (lines 299–309)

```
async def owner_member_id(connection: AsyncConnection, workspace_id: UUID) -> UUID | None
```

**Purpose**: Finds the workspace owner according to this file’s rule: the earliest-created member is the owner. There is no separate owner column here.

**Data flow**: It receives a database connection and workspace ID, reads the first member ordered by creation time and ID, and returns that member’s ID. If the workspace has no members, it returns nothing.

**Call relations**: `Seats.revoke` calls this to protect the owner from being unseated. `owner_conversation` calls it before looking for the owner’s most recent conversation.

*Call graph*: called by 2 (revoke, owner_conversation); 2 external calls (execute, select).


##### `create_member`  (lines 312–345)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID
```

**Purpose**: Creates a member row for a workspace and applies the shared automatic seating rule. It is meant to be the one place all member-creation paths use, so seating behavior stays consistent.

**Data flow**: It receives a database connection, workspace ID, and email address. It tries to insert a new member with a new UUID; if the insert succeeds, it applies automatic seating and returns the new ID. If another request already created the same member, it reads and returns the existing ID instead.

**Call relations**: Onboarding, teammate joins, and future member-creation surfaces can all use this function instead of writing directly to the member table. It uses the database’s conflict handling so two simultaneous creations collapse into one surviving member row.

*Call graph*: 4 external calls (__init__, execute, select, uuid4).


##### `owner_conversation`  (lines 348–371)

```
async def owner_conversation(connection: AsyncConnection, workspace_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Finds where the system should send a workspace-level question for the owner. It returns the owner’s most recently active member-bound conversation and the agent attached to it.

**Data flow**: It receives a database connection and workspace ID. It first finds the owner member, then searches for that owner’s conversations in the workspace ordered by latest update. It returns a pair of conversation ID and agent ID, or nothing if there is no owner or no owner conversation yet.

**Call relations**: This supports flows that need to ask the owner for action, such as granting a seat. It calls `owner_member_id` first, then performs the conversation lookup only if an owner exists.

*Call graph*: calls 1 internal fn (owner_member_id); 2 external calls (execute, select).


##### `member_workspaces`  (lines 374–382)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for jobs that should run over workspaces with at least one member. This keeps extensions from needing to know the member-table query themselves.

**Data flow**: It creates a small inner query function that selects distinct workspace IDs from the member table, then passes that query to `owner_candidates`. It returns a `WorkspaceCandidates` object that another job can use.

**Call relations**: This is used by scheduled or extension-driven seat-reporting work. The inner `with_a_member` function supplies the database query, and `owner_candidates` wraps it in the project’s standard workspace-candidate shape.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 379–380)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the actual database query for workspaces that have members. It is intentionally broad: if a workspace has any member row, it qualifies.

**Data flow**: It takes no outside arguments directly, builds a SQL query selecting distinct workspace IDs from the member table, and returns that query object for later execution.

**Call relations**: This helper exists inside `member_workspaces`. `member_workspaces` hands it to `owner_candidates`, which can use the query as part of choosing workspaces for a job.

*Call graph*: 1 external calls (select).


### Brokered account grants
These files drive the generic external account connection flow from hosted consent, through the core grant record, to callback completion and connector object management.

### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during connector authorization`

ufo expects an OAuth provider to give it a normal approval web address right away, and later to exchange a returned code for an account. Pipedream works a little differently: before the user can approve anything, the server must first make an asynchronous API call to Pipedream to create a temporary Connect token. This file fills that gap.

The main class, PipedreamOAuthProvider, represents one connector provider, such as an app backed by Pipedream. Its authorize_url method does not point straight to Pipedream. Instead, it points the browser to this extension’s own oauth route. That route can safely do the slower server-side work needed to ask Pipedream for a Connect token.

The oauth_route function is the bridge for both halves of the journey. On the way out, it checks the provider and state, creates a Pipedream Connect token for this exact workspace and connection attempt, and redirects the browser to Pipedream’s hosted consent page. On the way back, it finds the newest Pipedream account for that exact temporary user and sends ufo the account id as the “code.” Later, exchange fetches that exact account again and checks that it belongs to the expected Pipedream app before ufo records the connection.

A key safety detail is that each sealed state gets its own Pipedream external user id. Like giving every visitor a unique claim ticket, this prevents overlapping browser callbacks from accidentally picking up someone else’s newly connected account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first web address that ufo gives to the user’s browser when a connector needs approval. Instead of sending the browser directly to Pipedream, it sends it to this extension’s local OAuth bridge route so the bridge can create the needed Pipedream token first.

**Data flow**: It receives ufo’s sealed state value and the final callback address where ufo expects the result. It packages the provider name, state, and callback into query parameters, takes the scheme and host from the callback address, and returns a URL under /ext/pipedream/oauth. It does not contact Pipedream or change stored data.

**Call relations**: This is the start of the connection story for a Pipedream-backed provider. It relies on _origin to keep the bridge on the same public origin as the callback, then the browser later calls oauth_route with the values this function placed in the URL.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–63)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the account id returned through the bridge into the OAuthAccount object that ufo records as the connected account. It also double-checks that the account belongs to the expected Pipedream app before accepting it.

**Data flow**: It receives the returned code, which in this flow is really a Pipedream connected account id, plus the workspace id and sealed state. It recreates the Pipedream external user id for that workspace and state, asks the Pipedream client for that exact connected account, checks that its app matches this provider, and returns an OAuthAccount containing the account id. If the account belongs to a different app, it raises a Pipedream error instead of binding the wrong account.

**Call relations**: This runs after oauth_route has redirected back to ufo with a code. It calls Pipedream through the shared client and uses the same state-based external user id as the route, so the final grant is tied to the same connection attempt that started in authorize_url.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 66–109)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge between ufo and Pipedream. It starts the Pipedream consent process, receives the user after consent, and redirects the result back to ufo.

**Data flow**: It reads query parameters from the incoming HTTP request: provider, state, callback, and sometimes an outcome marker. If required values are missing, it returns an error response. If the provider is unknown, it returns a not-found response. If Pipedream reports a successful connection, it finds the newest connected account for this exact workspace-and-state user and redirects to ufo’s callback with the state and account id. If Pipedream reports failure, it returns a clear error message. If there is no outcome yet, it creates a Pipedream Connect token with success and error redirects back to this same route, builds the hosted Pipedream Connect Link for the requested app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: The browser arrives here first because PipedreamOAuthProvider.authorize_url pointed it here. On the start leg, this function hands off to Pipedream’s hosted consent page. On the return leg, it hands control back to ufo’s callback, where the provider’s exchange method later confirms and records the account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 112–116)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the public origin from a URL, meaning just the scheme and host, such as https://example.com. This is used to build bridge URLs that stay on the same site as ufo’s callback.

**Data flow**: It receives a URL string, parses it, and checks that it has an http or https scheme and a host name. If the URL is usable, it returns only the scheme and host. If it is missing those required parts, it raises an error because the OAuth bridge would not know where to send the browser.

**Call relations**: Both authorize_url and oauth_route call this helper when they need to construct a safe browser redirect address. It is a small guardrail that keeps the larger OAuth handoff from building broken or ambiguous URLs.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `core/src/ufo/grants.py`

`domain_logic` · `request handling and OAuth callback`

This file is the “consent and account link” part of the system. It solves the problem of letting an agent use a member’s external account without asking the deployment operator to pre-install that member’s private key or token. Think of it like giving a valet ticket, not the car keys: the grant stores a stable account id, while the broker keeps the real token and performs the actual provider work server-side.

The flow has two halves. First, `ConnectFlow.authorize` creates a provider login link. It seals the important details, such as workspace, agent, provider, member, conversation, and sharing choice, into an encrypted OAuth `state` value. OAuth is the common web pattern where a user approves access in a browser and is redirected back with a code. Later, `ConnectFlow.complete` opens that sealed state, exchanges the returned code with the provider, and records the resulting account id as a grant.

`GrantStore` is the database-facing piece. It records grants, lists active grants for an agent, revokes them, and changes whether they are shared. `ConnectHandoff` protects the chat handoff: only the member who received the private connect request can turn it into an authorization URL, and stale requests expire. The module also exposes one installed process-wide connect flow so tools and callbacks can find the same configured providers and encryption key.

#### Function details

##### `grant_sentinel`  (lines 34–38)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a special placeholder credential string for a connected account. The engine and the egress proxy can both recognize this same string later without having to share a separate registration table.

**Data flow**: It takes an external connected account id as text, prefixes it with the fixed grant sentinel marker, and returns the combined string. It does not read or change any stored state.

**Call relations**: This is a small shared convention used wherever the system needs a harmless stand-in for a real credential. Later parts of the system can match the sentinel back to a grant-derived account.


##### `OAuthProvider.provider`  (lines 76–76)

```
def provider(self) -> str
```

**Purpose**: Defines the provider name that an OAuth connector must expose, such as the slug used to identify the service. It is part of the provider contract rather than an implementation here.

**Data flow**: A concrete provider supplies this property. Callers read it to know which provider name should be recorded or compared; this protocol method itself produces no value on its own.

**Call relations**: Provider implementations are used by `ConnectFlow` when it builds authorization links and when it records completed grants. The protocol tells those implementations what information they must provide.


##### `OAuthProvider.host`  (lines 79–79)

```
def host(self) -> str
```

**Purpose**: Defines the provider host that a grant allows and meters at the egress proxy. In plain terms, it says which outside destination this grant is for.

**Data flow**: A concrete provider supplies the host string. Callers read it when a completed grant is saved so the proxy can later know what destination the grant admits.

**Call relations**: This is part of the descriptor consumed by `ConnectFlow.complete`, which stores the host through `GrantStore.record` after OAuth succeeds.


##### `OAuthProvider.authorize_url`  (lines 81–81)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a provider builds the browser URL where the user approves the connection. Each provider knows its own login and consent URL format.

**Data flow**: It receives a sealed state string and the callback URL that the provider should return to. A concrete provider turns those into an authorization URL for the user to open.

**Call relations**: Called through the provider descriptor during `ConnectFlow.authorize`, after the flow has sealed the workspace, agent, member, and provider details into state.


##### `OAuthProvider.exchange`  (lines 83–85)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a provider turns the returned OAuth code into the stable connected account id. This is where the provider or broker verifies that the browser approval really belongs to the intended workspace and flow.

**Data flow**: It receives the returned code, the callback URL, the workspace id, and the original state. A concrete provider checks and exchanges that information, then returns an `OAuthAccount` containing the broker-side account id.

**Call relations**: Called by `ConnectFlow.complete` after the callback state has been opened and trusted. Its result is passed to `GrantStore.record` so the account can be bound to the agent.


##### `OAuthProviderResolver.claims`  (lines 96–96)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open connector namespace recognizes a provider slug. This lets the system support provider names that are not pre-listed one by one.

**Data flow**: It receives a provider name and returns true or false after the resolver checks its catalog or rules. It does not record a grant by itself.

**Call relations**: Used by `ConnectFlow.validate_provider` when a connect request is first checked. A positive answer lets the request proceed even if the provider is not in the fixed provider map.


##### `OAuthProviderResolver.descriptor`  (lines 98–98)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuth provider descriptor for a provider name claimed by an open connector namespace. The descriptor gives `ConnectFlow` the same interface it would get from a pre-installed provider.

**Data flow**: It receives a provider slug and returns an object that follows the `OAuthProvider` contract. The returned descriptor can then create authorization URLs and exchange codes.

**Call relations**: Used inside `ConnectFlow._provider` when the named provider is not in the fixed provider map but a resolver exists.


##### `GrantStore.record`  (lines 161–214)

```
async def record(self, *, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> None
```

**Purpose**: Saves a completed grant in the workspace database, or refreshes the existing row if the same account is connected again. It refuses account ids containing control characters because those could corrupt later broker requests.

**Data flow**: It receives the workspace, agent, provider, connected account id, provider host, grantor, conversation, and sharing choice. It validates the account id, opens a workspace database transaction, inserts the grant, and on a duplicate grant updates the audit and sharing fields instead of creating another row. It returns nothing, but the database now contains the durable grant.

**Call relations**: This is called after OAuth has succeeded, especially from `ConnectFlow.complete`. It uses `workspace_tx` to write in the correct workspace transaction and `uuid4` to create a new row id when inserting.

*Call graph*: 2 external calls (workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 216–245)

```
async def active_grants(self, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]
```

**Purpose**: Loads the grants currently available to one agent in one workspace. These grants are what later tool execution and proxy rule building use to know which provider accounts the agent may use.

**Data flow**: It receives a workspace id and agent id, reads matching grant rows from the database, and converts each row into a `Grant` object containing provider, account id, host, grantor, and sharing flag. It returns those grants as an immutable tuple.

**Call relations**: Called by `core/src/ufo/loop/queue._grant_cli_env` when preparing grant-related environment information for a turn. It reads through `workspace_tx` and builds `Grant` objects from the selected rows.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 247–265)

```
async def revoke(self, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str) -> bool
```

**Purpose**: Deletes one agent’s grant for one provider account. This removes that agent’s ability to resolve and use that connected account, while leaving any other agent’s separate grant untouched.

**Data flow**: It receives workspace id, agent id, provider name, and account id. It deletes the matching row from the grant table and returns true if a row was removed, false if there was nothing to delete.

**Call relations**: This is the revoke path for connector grant objects. It uses `workspace_tx` and a SQL delete so the change takes effect as soon as the transaction completes.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `GrantStore.set_shared`  (lines 267–285)

```
async def set_shared(self, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str, shared: bool) -> bool
```

**Purpose**: Changes whether one grant is shared with the agent’s audience or kept private to the grantor. The sharing choice belongs to this specific agent-account binding.

**Data flow**: It receives workspace id, agent id, provider, account id, and the desired shared flag. It updates the matching grant row’s shared value and timestamp, then returns true if a row was updated.

**Call relations**: This supports share and unshare actions for connector grant objects. It writes through `workspace_tx` and uses a SQL update against the exact grant identity.

*Call graph*: 2 external calls (update, workspace_tx).


##### `ConnectFlow.authorize`  (lines 303–323)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Creates the provider authorization URL that a member should open to approve a connection. It seals all important context into the OAuth state so the callback can be trusted without keeping a separate pending row.

**Data flow**: It receives workspace, agent, provider, member, conversation, and sharing choice. It finds the provider descriptor, builds a `ConnectState`, encrypts it with Fernet, and asks the provider to turn that sealed state plus the callback URL into a browser URL. It returns that URL.

**Call relations**: This starts the OAuth handoff. It calls `ConnectFlow._provider` to find the provider descriptor and is used by `ConnectHandoff.authorize` when a valid chat connect request needs a URL.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 325–330)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a provider name can be connected before a request is accepted. This catches typos or unavailable connectors early.

**Data flow**: It receives a provider name. If the name is in the installed provider map, it succeeds; otherwise, it asks the optional resolver whether it claims the provider. If neither path accepts it, it raises `UnknownProvider`.

**Call relations**: This is the more thorough provider check, including the resolver’s possible live validation. It complements the cheaper `knows_provider` check used later under a database row lock.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 332–337)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether the process still has connect machinery for a provider. It avoids slower external validation while a locked turn row is being processed.

**Data flow**: It receives a provider name and checks local configuration: either the exact provider is installed, or an open resolver exists that can represent provider names. It returns true or false.

**Call relations**: Used by `ConnectHandoff.authorize` before minting or returning an authorization URL from a terminal connect request.


##### `ConnectFlow.bridge_workspace`  (lines 339–345)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to act for. This prevents a bridge request from pretending to belong to a different provider or callback URL.

**Data flow**: It receives the sealed state, provider name, and callback URL from a request. It opens the state, checks that the provider and callback match what was sealed, confirms the provider exists, and returns the workspace id. If anything does not match, it raises `ConnectStateInvalid` or `UnknownProvider`.

**Call relations**: Called indirectly by `connect_bridge_workspace`, which catches failures and turns them into a rejection. It relies on `ConnectFlow._open` to trust the state and `ConnectFlow._provider` to confirm provider availability.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 347–364)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth callback after the provider redirects back with a code. It exchanges the code for a connected account id and records the grant for the intended agent.

**Data flow**: It receives sealed state and an OAuth code. It opens the state, finds the provider descriptor, enters the sealed workspace context, asks the provider to exchange the code, records the returned account id through `GrantStore.record`, and returns a `GrantRecorded` summary.

**Call relations**: This is the second half of the OAuth handoff started by `ConnectFlow.authorize`. It calls `_open` and `_provider`, uses `ufo.workspace.ws` so database work happens in the right workspace, then delegates persistence to the store.

*Call graph*: calls 2 internal fn (_open, _provider); 2 external calls (__init__, ws).


##### `ConnectFlow._provider`  (lines 366–372)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the provider descriptor for a given provider name. It hides the difference between explicitly installed providers and providers supplied by an open resolver.

**Data flow**: It receives a provider name, checks the provider map, then asks the resolver to build a descriptor if one exists. It returns an `OAuthProvider` descriptor or raises `UnknownProvider`.

**Call relations**: Used by `ConnectFlow.authorize`, `ConnectFlow.bridge_workspace`, and `ConnectFlow.complete` whenever the flow needs provider-specific behavior such as building a URL or exchanging a code.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 374–379)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the OAuth state carried through the browser redirect. It rejects state that was changed, cannot be read, or is too old.

**Data flow**: It receives the state string, decrypts it with the configured Fernet key using the connect time limit, and parses it into a `ConnectState`. If decryption fails or the state expired, it raises `ConnectStateInvalid`.

**Call relations**: Used by `ConnectFlow.bridge_workspace` and `ConnectFlow.complete` before either path trusts any workspace, provider, or agent information from the browser.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 388–471)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a private terminal connect request into a memoized OAuth URL for the speaking member. It makes sure the request belongs to that member, has not expired, and still has a valid provider.

**Data flow**: It receives workspace id, turn id, and member id. It locks and reads the turn row, checks the speaker, terminal connect request, provider availability, and expiration times. If a usable URL was already created, it returns it; otherwise, it asks `ConnectFlow.authorize` for a new URL, stores it on the turn row, and returns it. If the request is invalid or stale, it raises `ConnectRequestInvalid`.

**Call relations**: This is the bridge between a chat turn and the OAuth flow. It uses database row locking through `workspace_tx` so two clicks do not create competing handoffs, validates the terminal frame with `TerminalFrame.model_validate`, and calls the flow only when a fresh authorization URL is needed.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 477–485)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow used by tools, surfaces, and callbacks. Passing `None` disables grants for deployments without a credential key.

**Data flow**: It receives a `ConnectFlow` object or `None` and stores it in the module-level `_installed_flow` variable. It returns nothing, but later callers will see the newly installed flow.

**Call relations**: Called during server setup or tests to provide the configured providers, encryption key, grant store, and callback URL. `installed_connect_flow` reads the value later.


##### `installed_connect_flow`  (lines 488–491)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the currently installed connect flow, or fails clearly if grants are not configured. This prevents code from silently pretending connect support exists when no credential key was set.

**Data flow**: It reads the module-level `_installed_flow`. If a flow is present, it returns it; if not, it raises `ConnectUnavailable`.

**Call relations**: Called by `connect_bridge_workspace` before verifying a browser bridge request. Other runtime surfaces can use the same accessor to share the one configured flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 494–503)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming connector browser bridge request is valid and, if so, returns the workspace id it belongs to. Invalid or unavailable connect setup is converted into `None` so the caller can reject the request.

**Data flow**: It receives a Starlette request, reads `state`, `provider`, and `callback` from the query string, gets the installed connect flow, and asks it to verify the bridge. It returns a workspace id on success or `None` if state, provider, or configuration checks fail.

**Call relations**: This is a safe wrapper around `installed_connect_flow().bridge_workspace`. It catches `ConnectStateInvalid`, `ConnectUnavailable`, and `UnknownProvider` so request-handling code gets a simple allow-or-reject answer.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `grant_summaries`  (lines 506–549)

```
async def grant_summaries(workspace_id: UUID, agent_id: UUID | None=None) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads grants as non-secret audit rows for operator or member-facing views. It can list the whole workspace or only one agent’s grants.

**Data flow**: It receives a workspace id and optionally an agent id. It builds a database query joining grants to agent names, filters by workspace and maybe agent, orders by provider, and converts rows into `GrantSummary` objects. It returns those summaries as an immutable tuple.

**Call relations**: Used by surfaces such as grant listing commands or connector object views. It reads through `workspace_tx` and does not need access to any secret because grants store account ids and audit details only.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

This file provides a small FastAPI route for completing an OAuth connection flow. OAuth is the common web pattern where a user approves access on one site, and that site redirects the browser back with a temporary code. Without this endpoint, the system could start an account connection, but it would have nowhere to receive the provider's answer and turn it into a usable grant.

The file creates a router mounted under `/v1`, and exposes `/v1/connect/callback`. When a provider redirects the browser here, the endpoint expects two pieces of information: `state` and `code`. The `state` is like a sealed claim ticket: it proves this callback belongs to a specific earlier connection request and carries the context needed to finish it. The `code` is the temporary provider-issued token that can be exchanged for the real account connection.

The route first asks the grants system for the installed connection flow. If connection support is not available, it returns a service error. It then rejects callbacks missing either required value. Finally, it asks the flow to complete the connection. Specific failures become clear HTTP errors: bad state becomes a bad request, and an unknown provider becomes not found. On success, it returns a plain text message telling the user the account was connected and to return to chat.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP endpoint that finishes an OAuth account connection after the outside provider redirects the user's browser back. It checks that the callback has the required proof and code, asks the grant system to complete the connection, and returns a simple human-readable success message.

**Data flow**: The function receives `state` and `code` from the callback URL query string. It gets the configured connection flow, rejects the request if connection support is unavailable or either value is missing, then passes the state and code to the flow so the provider code can be exchanged and recorded as a connected account. The output is either a plain text success response naming the connected provider and account, or an HTTP error response with an appropriate status code.

**Call relations**: FastAPI calls this function when a browser visits the registered connect callback route. Inside, it calls `ufo.grants.installed_connect_flow` to find the grant flow that knows how to finish the connection. When something is wrong, it raises `fastapi.HTTPException` so FastAPI can turn the problem into an HTTP error; when everything succeeds, it builds a `fastapi.responses.PlainTextResponse` to send the user back to chat with confirmation.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `request handling during connector OAuth connect flow`

ufo expects an OAuth provider to give it a normal authorization web address right away, but Composio needs an asynchronous API call to create that address. This file solves that mismatch by inserting a small redirect stop in the middle. Think of it like a reception desk: the user first arrives at ufo's Composio bridge, the bridge asks Composio where the real consent page is, then it sends the user there.

The main type, ComposioOAuthProvider, describes one Composio-backed connector provider. Its authorize_url method does not contact Composio directly. Instead, it builds a URL pointing to this extension's own /ext/composio/oauth route, carrying the provider name, the sealed state value, and the final callback URL.

The oauth_route function is that bridge route. On the first browser visit, it asks Composio for a connect link for the current workspace and redirects the user to Composio's hosted consent page. Later, Composio redirects back to the same route with a connected_account_id. The route then forwards the browser to ufo core's callback, using that account id as the OAuth code. Finally, exchange checks with Composio that the account really belongs to this workspace and provider before ufo binds it. Tokens stay inside Composio; ufo receives only an account reference.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL that ufo gives to the user's browser when a Composio-backed connector needs consent. Instead of pointing straight to Composio, it points to this extension's own OAuth bridge route so the bridge can create the real Composio consent link at request time.

**Data flow**: It receives a state value, which protects and identifies the connect attempt, and a redirect URI, which is where ufo core expects the final answer. It extracts the origin, meaning the scheme and host such as https://example.com, from that redirect URI, adds the provider name, state, and callback as query parameters, and returns a bridge URL under /ext/composio/oauth. It does not change stored data or contact Composio.

**Call relations**: This is called when ufo needs an authorization URL for a connector. It relies on _origin to make sure the callback has a usable web origin, and it uses URL encoding so the state and callback survive safely inside the browser URL. The URL it returns leads the browser into oauth_route, where the asynchronous Composio work happens.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–53)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This completes the connect flow after the browser returns with a Composio connected account id. It asks Composio to confirm that the account belongs to this workspace and matches the expected provider before ufo accepts it.

**Data flow**: It receives the code value, which in this bridge is really Composio's connected account id, plus the workspace id. It builds the expected Composio external user id for that workspace, asks the Composio client to look up and verify the connected account for this provider, and returns an OAuthAccount object that ufo can bind to the connector grant. It does not receive or store the provider's secret token.

**Call relations**: This runs after oauth_route has forwarded the browser to ufo core's callback with the connected account id as the code. It hands the verification step to the Composio client, which is the part that talks to Composio's API. This check is important because it prevents someone from injecting another workspace's account id into the callback.

*Call graph*: 1 external calls (composio_client).


##### `oauth_route`  (lines 56–93)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the HTTP route that bridges the browser between ufo and Composio. It starts the Composio consent journey, receives the result from Composio, and forwards that result back to ufo core.

**Data flow**: It reads query parameters from the incoming request: state, callback, provider, connected_account_id, and status. If state or callback is missing, it returns a bad-request response. If Composio has returned a connected_account_id, it redirects the browser to the callback with that id as the code. If Composio reports a status without an account id, it returns an error message instead of starting over silently. On the first leg, when there is no account id or status yet, it asks Composio for a connect link for the requested provider and current workspace, then redirects the browser to that link.

**Call relations**: The browser reaches this route from the URL built by ComposioOAuthProvider.authorize_url. On the start leg, the route calls _origin to build a return URL back to itself and asks the Composio client for the hosted consent link. On the return leg, it redirects to ufo core's callback, which then leads to ComposioOAuthProvider.exchange. The state and callback are carried through unchanged so ufo can still connect the result to the right member, agent, and conversation.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 96–100)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the safe base web address from a full URL, such as turning https://example.com/path?x=1 into https://example.com. It also rejects callback URLs that are not normal http or https web URLs.

**Data flow**: It receives a URL string and parses it into pieces. If the URL has an http or https scheme and a host name, it returns only the scheme and host. If either part is missing or the scheme is something else, it raises an error instead of returning a bad bridge address.

**Call relations**: ComposioOAuthProvider.authorize_url uses this helper to place the bridge route on the same origin as ufo's callback. oauth_route uses it again when building the URL that Composio should return to. This keeps the browser handoff anchored to a proper web host rather than trusting an incomplete or malformed callback.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling`

A connector here means a provider account, such as an external service account, that has been granted to an agent. The important rule is that the account connection itself is not created in this file. It must come from the special connect_account chat flow, because that flow can safely involve a third party and private credentials. This file instead gives the rest of the object system a safe view of those existing grants.

It turns each grant row into a named object. The name is built from the provider and account id, cleaned into a simple slug, with a short hash added if two accounts would otherwise get the same name. Listing shows the account, who owns it, and whether it is private or shared. Details show the provider, account id, sharing flag, and timestamps. Status adds audit-style facts such as the grantor, host, and agent.

The only allowed change is flipping the shared flag. That is like changing a room key from “only mine” to “available to the group,” or back again. Deleting a connector revokes this agent’s binding to that account, so the agent can no longer use that account for tools, syncs, or proxy rules. The underlying account may still be connected elsewhere for another agent.

#### Function details

##### `_slug`  (lines 47–48)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a provider name or account id into a safe, simple piece of an object name. It lowercases the text, replaces runs of non-letter-or-number characters with dashes, and removes extra dashes at the ends.

**Data flow**: It receives raw text, such as a provider name or account id. It normalizes that text into a lowercase dash-separated slug. It returns the cleaned string, which can be used as part of a connector object name.

**Call relations**: ConnectorObjects._named calls this helper while building stable names for grant rows. It is the small cleanup step before the code decides whether a plain name is enough or whether a hash suffix is needed.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `ConnectorObjects._owned_rows`  (lines 65–76)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: Builds the short list entries shown when someone lists connector objects. Each row says which provider account it is, whether it is private or shared, and who owns it.

**Data flow**: It reads the current turn context, asks _named for all grant rows mapped to object names, and turns each grant into an OwnedRow with a readable summary and an ObjectOwner. It returns all those rows as a tuple for the object framework to filter and display.

**Call relations**: The wider object system calls this when it needs a list view. This method depends on _named to translate raw grants into object names, then hands back ownership information so the shared/private visibility rules can be applied by the base object machinery.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, __init__).


##### `ConnectorObjects._detail`  (lines 78–88)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[ConnectorSpec] | None
```

**Purpose**: Returns the full object detail for one named connector. It is used when someone asks to inspect a specific connected account.

**Data flow**: It receives the context and an object name. It looks up that name through _named; if no grant matches, it returns nothing. If it finds a grant, it packages the provider, account id, shared flag, and timestamps into an ObjectDetail.

**Call relations**: The object framework calls this for a get or read-style operation on one connector. It uses _named as the shared lookup table, then wraps the grant facts in ConnectorSpec so callers see a clean object-shaped description rather than raw grant storage data.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, __init__).


##### `ConnectorObjects._status`  (lines 90–99)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Provides extra operational information about a connector, beyond its editable spec. This is useful for audit or troubleshooting, such as seeing who granted the account and which host or agent it belongs to.

**Data flow**: It receives the context and connector name. It looks up the matching grant through _named; if none exists, it returns nothing. If found, it returns a small dictionary containing the grantor member id, host, agent, and shared flag.

**Call relations**: The object framework calls this when it needs status information for a connector. Like the list and detail paths, it relies on _named so that status lookup uses the same object names as every other connector operation.

*Call graph*: calls 1 internal fn (_named).


##### `ConnectorObjects._apply_owned`  (lines 101–118)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorSpec, old: ConnectorSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Applies the only allowed edit to a connector: changing whether the connected account is shared. It deliberately rejects attempts to create a connector or change its provider or account id.

**Data flow**: It receives the context, connector name, requested spec, previous spec, and owner information. If there is no old object, or if anything besides the shared flag changed, it raises VerbNotSupported with a message telling the user to use connect_account. If the shared value is unchanged, it does nothing. Otherwise it finds the grant and asks the grant store to update that grant’s shared flag.

**Call relations**: The base owned-object flow calls this after it has already checked that the speaker is allowed to mutate the object. This method then enforces the connector-specific rule: apply is only for sharing changes. When a real change is needed, it hands the update to ctx.grants.set_shared so the grant store becomes the source of truth.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, model_copy).


##### `ConnectorObjects._delete_owned`  (lines 120–126)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Revokes this agent’s connection to a provider account. In everyday terms, it removes the agent’s permission to use that connected account.

**Data flow**: It receives the context, connector name, and owner information. It looks up the matching grant through _named, checks that the grant store is available, and asks that store to revoke the grant for this workspace, agent, provider, and account id. It does not return a value; the important result is the grant row being removed.

**Call relations**: The base owned-object flow calls this after permission checks confirm the speaker may delete the connector. This method translates the object name back into grant details and hands the actual removal to ctx.grants.revoke. After that, tools and syncs that depended on this agent’s grant stop resolving that account.

*Call graph*: calls 1 internal fn (_named).


##### `ConnectorObjects._named`  (lines 128–142)

```
async def _named(self, ctx: ToolContext) -> dict[str, GrantSummary]
```

**Purpose**: Builds the name-to-grant lookup used by every connector operation. It gives each connected account a predictable object name while avoiding collisions when different accounts clean down to the same slug.

**Data flow**: It reads grant summaries for the current workspace and agent. For each grant, it creates a plain name from the provider slug and account-id slug, then groups grants that would share the same name. If a group has one grant, that plain name is used. If multiple grants collide, it adds a short SHA-256 hash prefix from the account id to make each name distinct. It returns a dictionary from object name to grant summary.

**Call relations**: This is the shared lookup engine for _owned_rows, _detail, _status, _apply_owned, and _delete_owned. Those methods all start from a human-facing object name; _named is the bridge back to the underlying grant row they need to read, update, or revoke.

*Call graph*: calls 1 internal fn (_slug); called by 5 (_apply_owned, _delete_owned, _detail, _owned_rows, _status); 2 external calls (sha256, grant_summaries).


### GitHub App authorization
These files connect a workspace to a verified GitHub App installation and later exchange that installation for short-lived GitHub access.

### `extensions/coding/ufo_ext_coding/connect.py`

`io_transport` · `GitHub connection setup and browser redirect handling`

This file is the GitHub connection handshake for the coding extension. Its job is to let a workspace owner install the ufo GitHub App, then prove that the returned GitHub installation is one they are actually allowed to use. That proof matters because a GitHub installation ID is just a small number. If the system trusted that number by itself, someone could try to bind another organization’s GitHub App installation to their workspace.

The flow has two halves. First, `connect_github` gives the workspace owner a GitHub install link. That link includes a sealed piece of state, like a tamper-proof ticket, saying which workspace and credential slot this connection is for. GitHub later redirects the browser back to this extension.

On the return trip, `github_installed` reads GitHub’s authorization code and claimed installation ID. It does not trust the ID yet. Instead, `GitHubInstallExchange.reaches` trades the authorization code for the member’s own GitHub access token, then asks GitHub which ufo App installations that member can see. Only if the claimed installation appears in GitHub’s answer does the file bind that installation to the workspace credential slot.

The result stored in the workspace is not a raw trusted ID. It is bound through the credential system, so later token minting only works for an installation that passed this GitHub-backed check.

#### Function details

##### `connect_github`  (lines 43–67)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: This function starts the GitHub connection process. It gives the workspace owner a special GitHub App installation link that carries a short-lived, sealed marker tying the future redirect back to this workspace.

**Data flow**: It receives the current tool context and an empty input object. It checks that the speaker is the workspace owner, checks that this deployment has a GitHub App configured, asks the credential system to create a sealed authorization state, and then returns a text message containing the GitHub installation URL with that state attached.

**Call relations**: This is the user-facing starting point. It asks `ToolContext.speaker_is_owner` whether the current speaker may make a workspace-wide connection, calls `github_app_id` to confirm the GitHub App exists, and uses `ToolContext.begin_credential_authorization` to make the sealed state that GitHub will carry back later. It packages the final link using `TextContent` and `ToolResult`.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_owner); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 70–75)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: This function identifies which workspace a GitHub redirect belongs to. It does that by opening the sealed state that was placed on the original install link.

**Data flow**: It receives an HTTP request from the GitHub return path. It reads the `state` query parameter, asks the credential system to verify that this state was made for the GitHub installation slot and payload, and returns the workspace ID if the state is valid. If the state is missing or invalid, it returns nothing.

**Call relations**: This is a helper for routing the browser return to the right workspace. It delegates the actual tamper-checking and workspace lookup to `authorized_slot_workspace`, so the redirect is trusted only if it carries state that this deployment originally sealed.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 94–121)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: This method asks GitHub whether the authorizing user can really access the claimed GitHub App installation. It is the main safety check that stops someone from connecting an installation ID they do not control.

**Data flow**: It receives a GitHub authorization code and a claimed installation ID. It sends the code, client ID, and client secret to GitHub to get the user’s access token. If GitHub does not return a token, it raises `GitHubAuthorizationError`. With the token, it asks GitHub for the installations visible to that user, filters that list to this ufo GitHub App, and returns `true` only if the claimed installation ID is in that list.

**Call relations**: This method is called during the redirect flow by the object returned from `install_exchange`. It uses `httpx.AsyncClient` to talk to GitHub’s OAuth and API endpoints. If GitHub rejects the authorization, it signals that to the caller with `GitHubAuthorizationError`, allowing `github_installed` to show an appropriate failure page.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 124–135)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: This function builds the object that can perform the GitHub authorization exchange. It gathers this deployment’s GitHub App identity from configuration and environment variables.

**Data flow**: It reads the configured GitHub App ID and the environment variables holding the GitHub OAuth client ID and secret. If no GitHub App is configured, it raises an error. Otherwise, it returns a `GitHubInstallExchange` containing those values.

**Call relations**: This is used by `github_installed` when GitHub sends the browser back after installation. It calls `github_app_id` to learn which App this deployment represents, then constructs `GitHubInstallExchange`, which performs the actual call to GitHub.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 138–167)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This function finishes the GitHub installation callback. It validates GitHub’s return data, proves the claimed installation belongs to the authorizing member, and then stores the installation for the workspace.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error page. Otherwise, it creates an exchange object, checks whether the authorizing GitHub user reaches the claimed installation, and rejects the request if not. If the check succeeds, it binds the installation ID into the workspace credential slot and returns a success page.

**Call relations**: This is the main return-leg handler after `connect_github` has sent the user to GitHub. It calls `install_exchange` to get the GitHub-checking helper, then relies on that helper’s `reaches` method. For every user-visible outcome, success or failure, it calls `_page` to build a small HTML response.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 170–178)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: This small helper creates a simple HTML page for the browser after the GitHub redirect. It keeps success and error responses readable for the person completing the connection.

**Data flow**: It receives a message and an HTTP status code. It wraps the message in a minimal HTML document and returns a `Response` with that status code and `text/html` media type.

**Call relations**: This function is called by `github_installed` whenever the redirect flow needs to tell the user what happened. It hands the finished HTML response to the HTTP layer through `Response`.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup during git access`

This file solves a safety problem around GitHub access. A workspace may have installed this project’s GitHub App, but the workspace should not store a powerful secret. Instead, it stores a sealed installation reference: a protected value that proves “this workspace is allowed to use this installation.” When Git access is needed, this file opens that seal, asks GitHub for a temporary installation token, and gives that token to the rest of the system.

The important idea is that a raw installation ID is not trusted. It is just a small number someone could type in. So the code only accepts an installation value if it can be opened with the project’s credential protection and matches the workspace and slot. If the seal cannot be opened, the code fails rather than quietly using some other token, because using the wrong identity could access repositories under the wrong permission grant.

The file also avoids asking GitHub too often. Tokens last about an hour, so `GitHubAppTokens` keeps a cache and refreshes only when a token is close to expiring. If two tasks ask for the same token at the same time, they share one in-progress minting task instead of making duplicate GitHub requests. The deploy’s GitHub App ID and private key come from environment variables, and the private key is used to sign a short-lived JWT, which is a signed proof sent to GitHub to request the installation token.

#### Function details

##### `_segment`  (lines 47–48)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: This helper prepares one piece of a JWT, which is a signed token format used to prove the app’s identity to GitHub. It turns a small JSON object into the compact, URL-safe text form that JWTs require.

**Data flow**: It receives a dictionary of values, converts it to tightly packed JSON text, encodes that text in URL-safe base64, and removes padding characters. The result is a byte string ready to become the header or body part of a JWT.

**Call relations**: It is used inside `GitHubAppTokens._jwt` when building the signed proof for GitHub. That JWT is later sent to GitHub when requesting an installation access token.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 71–84)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This checks whether a workspace has a readable GitHub App installation binding. It answers only whether the app installation is properly present; it does not mint or fetch a GitHub token.

**Data flow**: It takes a workspace ID and credential store, looks in the configured credential slot, and tries to open the stored sealed installation value. If the slot is missing, or the seal cannot be opened, it returns `False`; if the seal opens correctly, it returns `True`. If the seal is unreadable, it also writes a warning so operators have a clue about the bad binding.

**Call relations**: Other parts of the credential flow can call this before deciding whether GitHub App access is available. It relies on the credential store to read the saved value and on `open_installation` to prove that the value really belongs to this workspace and slot.

*Call graph*: calls 1 internal fn (get); 2 external calls (open_installation, warn).


##### `GitHubAppTokens.secret`  (lines 86–102)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This is the main method that returns the GitHub installation token to use as the credential secret. If the workspace has no App installation binding, it returns nothing so another credential path can be used.

**Data flow**: It receives a workspace ID and credential store, reads the sealed installation value, opens it, and uses the workspace plus installation as a cache key. If a still-fresh token is already cached, it returns that token. If not, it starts or joins one shared minting task, waits for it safely, and returns the newly minted token.

**Call relations**: This is the method the wider credential system calls when it needs an actual secret for GitHub access. It hands off token creation to `GitHubAppTokens._mint`, and it uses the cache and in-progress task table so repeated or simultaneous requests do not create unnecessary GitHub API calls.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 104–112)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: This performs one token minting operation and records the result in the cache. It also cleans up the “minting in progress” marker when the operation is done.

**Data flow**: It receives the cache key and installation ID, asks GitHub for a fresh installation token through `GitHubAppTokens._installation_token`, stores the returned token and expiry time under that key, and returns the same token data. Whether it succeeds or fails, it removes its own pending-task entry if it is still the current task for that key.

**Call relations**: It is started by `GitHubAppTokens.secret` when no usable cached token exists. It is the bridge between the public credential request path and the lower-level GitHub API exchange in `GitHubAppTokens._installation_token`.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 114–146)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: This talks to GitHub and exchanges the app’s signed proof for an installation access token. It is where the temporary GitHub credential is actually requested.

**Data flow**: It receives an installation ID, builds a signed JWT with `GitHubAppTokens._jwt`, and sends an HTTP POST request to GitHub’s installation access-token endpoint. If GitHub returns success, it reads the token and expiry time from the response and returns them. If the network fails, GitHub rejects the request, or the response cannot be understood, it raises `CredentialMintFailed` instead of falling back to another identity.

**Call relations**: It is called by `GitHubAppTokens._mint` as the actual minting step. It depends on `GitHubAppTokens._jwt` to prove the app’s identity before GitHub will issue the installation token.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 3 external calls (__init__, fromisoformat, AsyncClient).


##### `GitHubAppTokens._jwt`  (lines 148–155)

```
def _jwt(self) -> str
```

**Purpose**: This creates the short-lived signed JWT that identifies this deploy’s GitHub App to GitHub. Think of it as a stamped letter: it says which app is asking, when the request is valid, and carries a cryptographic signature GitHub can verify.

**Data flow**: It reads the current time, creates a JWT header and body, encodes both using `_segment`, signs them with the app’s RSA private key, and joins the pieces into the final JWT string. The output is not the repository access token; it is the proof used to request that token.

**Call relations**: It is called by `GitHubAppTokens._installation_token` immediately before making the GitHub API request. It uses `_segment` to format the JWT parts and the configured private key to make the signature.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `app_tokens`  (lines 158–169)

```
def app_tokens(installation_slot: str) -> GitHubAppTokens
```

**Purpose**: This builds a ready-to-use `GitHubAppTokens` object from deploy-time environment variables. It makes missing or invalid GitHub App configuration fail loudly instead of silently causing confusing credential behavior later.

**Data flow**: It receives the name of the credential slot that stores installation bindings, reads `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` from the process environment, parses the private key, checks that it is an RSA key, and returns a configured `GitHubAppTokens` instance. If the key is the wrong kind, it raises an error.

**Call relations**: Startup or extension setup code calls this to create the token minter used later by the credential system. The returned object is what later answers `bound` and `secret` requests for workspaces that may have installed the GitHub App.

*Call graph*: 2 external calls (__init__, load_pem_private_key).

## 📊 State Registers Touched

- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-auth-session` — The login and token state that proves who a user or client is across gateway, web, terminal, and admin requests.
- `reg-onboarding-state` — The invite codes, email claim codes, onboarding records, and first-workspace setup state for new hosted users.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-connection-grants` — The saved approvals and safe account handles for connected external accounts such as Slack, GitHub, Composio, and Pipedream.
- `reg-seat-entitlements` — The shared seat and access-limit state that decides which members may use the agent in a workspace.
- `reg-surface-installations` — The stored links between outside surfaces, workspaces, channels, conversations, and agents.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.

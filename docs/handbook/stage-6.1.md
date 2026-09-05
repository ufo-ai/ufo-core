# Login, session, and signed-token checks  `stage-6.1`

This stage is shared security support that runs before protected parts of the system do their work. Its job is to prove that a request is carrying a trustworthy “label” about who it is for, without needing to keep all session details in server memory.

The bearer token code creates and checks signed login tokens for UFO members. These tokens say which user and workspace the request belongs to, and the signature proves the text was not changed. The surface token code does a narrower job for public entry routes called surfaces. It identifies the workspace early, before normal login or cookies are available, like a sealed address label rather than a full access pass.

The token signing code is the common stamp maker and stamp checker. Other parts use it to pack small pieces of data into tamper-resistant tokens. The sandbox ingress token code adds time limits and purpose checks for access to sandboxed app ports, so preview links, cookies, and reports cannot be swapped or misused.

## Files in this stage

### Workspace identity tokens
Member login and public surface tokens establish workspace-aware identity before normal protected handling is available.

### `core/src/ufo/harness/auth/bearer.py`

`domain_logic` · `login and request authentication`

This file is the shared rulebook for UFO bearer tokens. A bearer token is like a stamped wristband: whoever carries it can prove they were admitted, but only if the stamp is genuine and not expired. Here, the “stamp” is an HMAC signature, which means a short proof made with a secret key so the token cannot be changed without detection.

The token contains three pieces of information: the workspace id, the member email address, and an expiry time. The file turns that information into compact JSON, encodes it safely for use in text, and signs it with the secret stored in the UFO_TOKEN_SECRET environment variable. Later, the same file can verify the signature, reject expired tokens, and return only trusted claims.

This matters because several surfaces need the same answer to a security question: “Does this request really belong to this member and workspace?” Without this single codec, token creators and token checkers could drift apart, causing valid users to be rejected or, worse, invalid tokens to be accepted.

The file also names the fixed browser routes and cookie used for login and logout, so the session token has one consistent place to live in web flows.

#### Function details

##### `mint_token`  (lines 37–54)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed bearer token for a workspace and email address. It is used when the system wants to issue a login proof that can later be checked without looking anything up in server-side session storage.

**Data flow**: It receives a signing secret, a workspace id, an email address, a time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates an expiry time, turns the claims into JSON, encodes that JSON, signs the encoded body with HMAC-SHA256, and returns one string containing the body and signature separated by a dot. If no secret is provided, it raises an error instead of making an unsafe token.

**Call relations**: This is the token-making half of the file. Other parts of the system can mint tokens through this shared format, and the checking half later uses the same signing rules to decide whether a presented token is genuine.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 57–80)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the trusted workspace and email claims. If anything looks wrong, it returns nothing instead of trusting partial data.

**Data flow**: It takes a token string and optionally a current timestamp. It reads the secret from the environment, splits the token into its encoded body and signature, recomputes the expected signature, and compares the two safely so timing differences do not leak information. If the signature matches, it decodes the body, parses the JSON, checks that the workspace, email, and expiry fields have the expected types, and rejects the token if it has expired. The output is a pair of strings, workspace id and email, or None if verification fails.

**Call relations**: This is the central checkpoint used by both verify_token and workspace_claim. Those functions ask it to do the hard security work first, then apply their own extra rule: matching a known workspace or turning the workspace claim into a UUID.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 83–94)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Checks that a token is valid for one specific workspace, and returns the member email if it is. This is useful when a process is tied to a single workspace and must reject tokens minted for any other workspace.

**Data flow**: It receives a token, the expected workspace id, and optionally a current timestamp. It first asks verified_claims to prove the token is signed and unexpired. If that succeeds, it compares the token’s workspace claim with the expected workspace id. The output is the lowercased email address when everything matches, or None when the token is invalid, expired, or belongs to another workspace.

**Call relations**: This function builds on verified_claims. It is the stricter path for places that already know which workspace they serve, so after the shared token check it adds the per-workspace guardrail.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 97–108)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace id from a valid bearer token. This is useful in shared services where one running process may serve many workspaces and must learn the workspace from each request.

**Data flow**: It receives a token and optionally a current timestamp. It asks verified_claims to confirm the token is signed and unexpired, then tries to turn the workspace string into a UUID, which is the standard structured form for workspace ids. It returns that UUID on success, or None if verification fails or the workspace value is not a valid UUID.

**Call relations**: This function also depends on verified_claims for the security decision. After that shared check, it converts the trusted workspace text into a form callers can safely use for routing or scoping a request.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 111–115)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the process environment. This keeps verification tied to the deployment’s configured secret instead of accepting a key from untrusted callers.

**Data flow**: It looks up the UFO_TOKEN_SECRET environment variable. If the value exists, it returns it. If it is missing or empty, it raises a runtime error because token verification cannot be secure without the secret.

**Call relations**: verified_claims calls this before checking a token signature. By keeping secret lookup here, the rest of the verification flow has one clear source for the key.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 118–119)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the token body from URL-safe base64 text back into bytes. It exists because the token stores JSON in a compact text form that can safely travel in cookies and headers.

**Data flow**: It receives an encoded string from the token body. It adds back any missing padding characters required by base64 decoding, then decodes the text into raw bytes. Those bytes are then ready to be parsed as JSON.

**Call relations**: verified_claims calls this after the token signature has matched. It is a small helper in the checking pipeline: signature first, decode second, JSON parsing after that.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/harness/auth/surface_token.py`

`domain_logic` · `request handling`

Some routes are reached only through a link. At that early point, the system may not have a browser cookie or any already-known workspace to look up. This file solves that bootstrapping problem by putting the needed route claims into a signed token that can safely travel in a URL.

Think of the token like a sealed envelope. Anyone can carry it, but only the server with the shared secret can make or verify the seal. The contents are plain claim strings, such as identifiers needed to find the right workspace, plus one reserved claim naming the surface that minted the token. When the token is checked, the surface name must match the route that is trying to use it. This stops a token made for one surface from being reused at another surface’s route.

The important boundary is that this file proves only that the token was created by this deployment and has not been changed. It does not decide whether the request is allowed. There is no expiry here, and every route must still do its own access checks afterward. The shared signing secret comes from the same environment variable used by bearer-token authentication, so token creation and verification depend on that deployment-level secret being configured.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: Creates a signed token for one named surface. Callers use it when they need to put trusted route claims into a URL without exposing the signing secret.

**Data flow**: It receives a surface name and a mapping of string claims. It first rejects an empty surface name and rejects any payload that tries to set the reserved surface claim itself. It then builds a small JSON body containing the surface name plus the caller’s claims, reads the deployment secret through _secret, signs the JSON bytes, and returns the finished token string.

**Call relations**: This is the token-making half of the flow. When a surface needs to produce a permanent link-like address, it calls this function. The function gets the secret from _secret and hands the prepared JSON body to sign_token, which applies the cryptographic signature.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: Checks whether a token is genuine for a specific surface and, if so, returns the claims inside it. It returns None instead of claims when the token is forged, malformed, made for another surface, or contains non-string claim data.

**Data flow**: It receives the expected surface name and a token string. It reads the deployment secret through _secret, asks verify_token to confirm the signature and recover the token body, then parses that body as JSON. If the result is not a dictionary, if its reserved surface value does not match the expected surface, or if any remaining claim key or value is not a string, it returns None. Otherwise it removes the reserved surface field and returns the remaining claims as a dictionary.

**Call relations**: This is the token-reading half of the flow. A surface route calls it before using URL claims to identify anything. It relies on verify_token to prove the seal is valid, then applies this file’s extra rule that the token must belong to the same surface that is asking.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: Fetches the deployment’s token-signing secret from the environment. This keeps both token creation and token checking tied to the same configured secret.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If a non-empty value is present, it returns that string. If the variable is missing or empty, it raises an error because signing and verifying tokens would not be safe or possible.

**Call relations**: Both mint_surface_token and verify_surface_token call this helper right before using the signing system. It is the single local doorway to the shared secret, so the rest of this file never hard-codes or stores the key itself.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### Shared token signing
The common signing helper creates and verifies tamper-resistant opaque token payloads used by authentication flows.

### `core/src/ufo/harness/auth/token_signing.py`

`domain_logic` · `request handling / authentication checks`

This file is a compact security helper for making tokens that are safe to pass around as text. A token here has two parts: a base64url-encoded payload, which is a web-safe text form of raw bytes, and a signature, which is a short proof made with HMAC. HMAC means a cryptographic fingerprint created from the payload plus a secret key; anyone without the secret cannot create the same fingerprint for a changed payload.

The important idea is that the payload is not encrypted. It is only encoded and signed. That is like putting a message in a clear envelope with a tamper-evident seal: people may be able to read the envelope contents if they have the token, but they cannot alter it without breaking the seal.

The file provides low-level signing for any message, then builds a token format on top of it: `body.signature`. When checking a token, it first makes sure the dot-separated shape is present, then recomputes the expected signature using the same secret, compares it safely, and finally decodes the body back into bytes. If anything is missing, changed, or unreadable, it raises `SignedTokenError` so callers can treat the token as invalid.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: Creates a standalone signature for a message using a shared secret. This is useful when the system wants proof that a byte string has not been changed since it was signed.

**Data flow**: It receives a secret key as bytes and a message as bytes. It runs the message through HMAC with SHA-256, which produces a fixed-size cryptographic fingerprint, then turns that fingerprint into base64url text and removes padding characters. It returns that text signature and does not change any outside state.

**Call relations**: This is the basic seal-making step used by the rest of the file. `sign_token` calls it when building a full token, and `verify_detached` calls it to recreate the expected signature before comparing it with the one it was given.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: Checks whether a given signature really matches a message and secret. It answers yes or no without decoding or interpreting the message itself.

**Data flow**: It receives the secret, the original message bytes, and a signature string to check. It signs the message again with `sign_detached`, then compares the expected signature with the supplied one using a timing-safe comparison, which avoids leaking clues through tiny timing differences. It returns `true` if they match and `false` if they do not.

**Call relations**: This is the signature-checking step used by `verify_token`. When a full token is being verified, `verify_token` gives this function the token body and signature so it can decide whether the token was tampered with.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Builds a complete signed token from raw payload bytes. Callers use it when they need a compact text token that can later be checked for tampering.

**Data flow**: It receives a secret key and payload bytes. First it encodes the payload into base64url text so it can travel safely in places like headers, URLs, or JSON strings. Then it signs that encoded body with `sign_detached` and joins the body and signature with a dot. It returns the finished token string.

**Call relations**: This is the outward-facing token creation function in this file. It relies on `sign_detached` for the cryptographic signature, then packages the signed body into the `body.signature` format that `verify_token` expects later.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Validates a signed token and returns its original payload bytes if it is trustworthy. It rejects malformed, tampered, or unreadable tokens with `SignedTokenError`.

**Data flow**: It receives a token string and the shared secret. It splits the token at the first dot into a body and signature, rejects it if either part is missing, and asks `verify_detached` whether the signature matches the body. If the signature is valid, it adds any needed base64 padding back and decodes the body into the original bytes. The result is the payload bytes; on failure, it raises a clear token-specific error.

**Call relations**: This is the main checking doorway for callers that receive tokens from outside. It delegates the cryptographic match to `verify_detached`, uses base64 decoding to recover the payload, and turns shape, signature, or decoding problems into `SignedTokenError` so higher-level authentication code can reject the token cleanly.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### Sandbox ingress tokens
Sandbox ingress tokens authorize time-limited access to sandboxed app ports while keeping link types distinct.

### `core/src/ufo/harness/sandbox/ingress_token.py`

`domain_logic` · `link creation and request handling`

A sandbox may expose a local port so a browser can view an app, but that port must not be open to anyone who guesses a URL. This file solves that by putting the permission into a signed token, like a sealed ticket. The ticket says which workspace, conversation, and port it belongs to, when it expires, and sometimes which enclosing frame or shipped app bundle is involved.

There are three token kinds. A view token is used in the first link a browser opens. A session token is later stored as a cookie for that browser origin. A report token is used for reporting a site that could not be reached. The token kind is part of the signed data, so a session cookie cannot be reused as a fresh view link, and a view link cannot be pasted in as a session cookie.

The file also defines small frozen data objects for the verified claims. “Frozen” means their values cannot be changed after creation, which helps treat verified permissions as facts. When checking a token, the code verifies the signature, checks the expected kind, parses identifiers and ports, rejects impossible ports, and refuses expired tokens. Without this file, sandbox access would either be too trusting or every caller would have to duplicate fragile security checks.

#### Function details

##### `mint_ingress_token`  (lines 81–98)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns trusted ingress claims into a signed token string. Someone uses it when they need to give a browser or service a temporary, tamper-resistant permission to reach exactly one sandbox target.

**Data flow**: It receives an IngressClaims object and the token kind to create. It copies the important fields into a plain JSON body, including optional shipped-bundle and framing information when present. It reads the shared deploy secret, signs the JSON with that secret, and returns the signed token text.

**Call relations**: This is the issuing side of the flow. It calls ingress_secret to get the shared secret and then hands the prepared JSON body to sign_token, so later verify_ingress_token can prove the token came from this system and was not edited.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 101–140)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether a token is genuine, still alive, and meant for the exact ingress use being served. If the token is wrong, expired, malformed, or for a different kind of hop, it raises IngressTokenError instead of returning unsafe data.

**Data flow**: It receives a token string, the current time, and the expected token kind. It reads the shared deploy secret, verifies the signature, decodes the JSON, checks that the kind matches, builds IngressClaims from the payload, and validates port numbers and expiry time. On success it returns clean, typed claims; on failure it stops with a clear ingress-token error.

**Call relations**: This is the checking side that mirrors mint_ingress_token. It calls ingress_secret so it uses the same deploy secret as the minting path, uses verify_token to prove the signature, uses json.loads to read the body, and creates ShippedClaim, FramerClaim, and IngressClaims objects only after the token has passed the main checks.

*Call graph*: calls 1 internal fn (ingress_secret); 8 external calls (__init__, __init__, __init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 143–150)

```
def ingress_secret() -> str
```

**Purpose**: This function fetches the shared secret used to sign and verify ingress tokens. It makes missing configuration fail loudly, because without the secret the system cannot safely mint or check access tickets.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value exists, it returns that string. If it is missing or empty, it raises a RuntimeError explaining that the secret must be set.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this helper before doing cryptographic work. That keeps the minting and checking paths tied to the same deploy-wide secret instead of letting callers pass in different or accidental secrets.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).

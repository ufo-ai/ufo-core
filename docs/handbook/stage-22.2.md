# Signed Login, Surface, and Ingress Tokens  `stage-22.2`

This stage is shared behind-the-scenes security support. It gives the system a way to pass small pieces of identity or access information around without keeping everything in a server-side session. The trick is signing: the data is bundled with proof made from a secret key, so later code can tell if anyone changed it.

The reusable base is token_signing.py. It knows how to make and check these signed tokens, and how to reject tokens that are broken, altered, or made with the wrong secret. bearer.py uses that machinery for member login tokens. These say which workspace and email address the logged-in user belongs to. surface_token.py uses the same idea for public “surface” routes, where the system may need workspace or claim information before cookies or database records are available. It proves the claims were issued by the system, but does not itself grant permission.

ingress_token.py applies the pattern to sandboxes. It creates short-lived tokens for one browser to reach one specific sandbox port, so access cannot be guessed, moved elsewhere, or reused forever.

## Files in this stage

### Member and Surface Claims
Login and public surface tokens establish signed workspace, email, and route claims before deeper authorization or session lookup.

### `core/src/ufo/auth/bearer.py`

`domain_logic` · `login, request authentication`

This file is the shared rulebook for UFO bearer tokens. A bearer token is like a signed wristband: whoever presents it can be treated as the named member, but only if the signature proves UFO issued it and the expiry time has not passed. The token contains three pieces of information: the workspace id, the member email address, and an expiry timestamp. That information is turned into compact JSON, encoded safely for URLs, and signed with a secret key from the environment variable UFO_TOKEN_SECRET. The signature uses HMAC, which is a way to prove that the token was made with the shared secret without putting the secret inside the token. Verification does the reverse: split the token, recompute the signature, compare it safely, decode the payload, check that the fields look right, and reject expired tokens. The file also defines the fixed browser paths and cookie name used for login and logout. Without this file, different parts of the system could disagree about what a valid token looks like, or worse, accept forged, expired, or wrong-workspace credentials.

#### Function details

##### `mint_token`  (lines 37–54)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed login token for one workspace and one email address. It is used when the system needs to issue proof that a member has authenticated, such as after login or local setup.

**Data flow**: It takes a secret key, a workspace id, an email address, a time-to-live, and optionally a fixed current time. It cleans the email by trimming spaces and lowercasing it, builds a small payload with the workspace, email, and expiry time, encodes that payload, signs the encoded body with the secret, and returns one string containing both the body and the signature.

**Call relations**: This is the issuing side of the token story. Later, callers use the verification functions in this same file to read and trust only tokens that match this exact format and signature.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 57–80)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a token is genuine and still valid, then returns the workspace id and email it proves. If anything is wrong, it returns nothing instead of trusting partial information.

**Data flow**: It reads the signing secret from the environment, splits the token into its encoded body and signature, recomputes what the signature should be, and compares the two in a timing-safe way. If the signature matches, it decodes the body, checks that it is a JSON object with string workspace and email fields plus an integer expiry time, rejects expired tokens, and finally returns the workspace and email.

**Call relations**: This is the central verifier. verify_token calls it when a deployment already knows which workspace it should accept, and workspace_claim calls it when the workspace must be discovered from the token itself. It relies on _secret to get the key and _b64url_decode to unpack the encoded payload.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 83–94)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Checks that a token belongs to a specific workspace and returns the authenticated member email. This stops a valid token from one workspace being used in another workspace.

**Data flow**: It receives a token, the expected workspace id, and optionally a current timestamp for testing or controlled checks. It first asks verified_claims to prove the token is real and unexpired, then compares the token's workspace claim with the expected workspace; if they match, it returns the email in lowercase, otherwise it returns nothing.

**Call relations**: This function builds on the general token checker for places where the process is already tied to one workspace. It does not decode or sign anything itself; it delegates the hard security checks to verified_claims and then adds the workspace match.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 97–108)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Finds the workspace named by a valid token. This is useful when one shared service handles requests for many workspaces and must decide the workspace from the request itself.

**Data flow**: It takes a token and optionally a current timestamp. It asks verified_claims to prove the token is genuine and unexpired, then tries to convert the workspace string into a UUID, which is the standard structured form of a workspace id; if that conversion fails, it returns nothing.

**Call relations**: This function is the shared-service counterpart to verify_token. Instead of checking against a preselected workspace, it uses verified_claims to trust the token first, then hands back the workspace id so the rest of the request can be scoped correctly.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 111–115)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the process environment. Verification cannot be safe without this secret, so the function fails loudly if it is missing.

**Data flow**: It looks up UFO_TOKEN_SECRET in environment variables. If a non-empty value exists, it returns that value; if not, it raises an error telling the operator that the verifier is not configured.

**Call relations**: verified_claims calls this before checking any token. That keeps the secret out of callers' hands and makes this file the one place responsible for fetching the key used to trust member bearers.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 118–119)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the token payload from URL-safe base64 back into bytes. It also restores any missing padding characters, because the token format strips them to keep the string shorter and cleaner.

**Data flow**: It receives the encoded payload text, adds the required equals-sign padding based on the length, decodes the URL-safe base64 text, and returns the original bytes that can then be parsed as JSON.

**Call relations**: verified_claims uses this helper after the signature has been accepted. It is a small decoding step in the larger path from raw token string to trusted workspace and email claims.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/auth/surface_token.py`

`domain_logic` · `request handling`

Some routes are reached only by a link, so the system cannot rely on a browser cookie to know which workspace or context the request belongs to. This file solves that by putting the needed claims into an opaque signed token, like a sealed address label on an envelope. Anyone can carry it in a URL, but they cannot change what it says without breaking the seal.

The token body is ordinary JSON data, but it is signed with a shared secret from the environment. That signature is an HMAC-style proof, meaning the system can later check that the token was made by this deployment and was not edited. The token also stores the name of the surface that minted it. When a route verifies a token, it must ask for the same surface name, so a token made for one surface cannot be reused as an address for another.

A key point is that this is an address, not permission. Tokens do not expire here, and successful verification only returns claims such as workspace identifiers. The route still needs to apply its normal access checks afterward. The file is strict about claim shape: all returned claim keys and values must be strings, because these values are meant to survive transport through URLs.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: Creates a signed token for one named surface, carrying string claims that can later be recovered from a URL. It rejects empty surface names and refuses payloads that try to set the reserved surface field themselves.

**Data flow**: It receives a surface name and a mapping of claim names to claim values. It adds the surface name under the reserved surface claim, turns the combined data into compact, consistently ordered JSON bytes, reads the signing secret, and passes both to the token signing helper. The result is a string token that can be placed in a link.

**Call relations**: This is the minting side of the flow. When a surface needs to create its permanent signed address, it calls this function; this function asks _secret for the deployment secret and hands the final byte payload to sign_token so the token cannot be changed unnoticed.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: Checks whether a token is valid for a specific surface and, if so, returns the claims inside it. If the token is forged, malformed, for another surface, or contains non-string claims, it returns None instead of trusting it.

**Data flow**: It receives the expected surface name and a token string. It reads the shared secret, asks the token verifier to check the signature and recover the raw body, parses that body as JSON, confirms it is a dictionary for the requested surface, removes the reserved surface field, and checks that every remaining key and value is a string. The output is either a clean dictionary of claims or None when any check fails.

**Call relations**: This is the receiving side of the flow. A route that has only a link token can call this before choosing what workspace or context to use; it relies on _secret for the same deployment secret and verify_token for the tamper check, then performs the surface-name and claim-shape checks itself.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: Fetches the shared token-signing secret from the process environment. It fails loudly if the secret is missing, because signing or verifying these tokens would be unsafe or impossible without it.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If a value is present, it returns that string; if not, it raises a runtime error telling the operator that the secret must be set.

**Call relations**: Both mint_surface_token and verify_surface_token call this before using the lower-level signing helpers. It keeps secret lookup in one place so token creation and token checking use the same configuration rule.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### Signing Primitives
Shared token-signing helpers provide the reusable tamper-detection and validation mechanics used by higher-level token types.

### `core/src/ufo/auth/token_signing.py`

`domain_logic` · `cross-cutting authentication`

This file is a compact security helper for making opaque tokens. “Opaque” means the token is just a blob to most of the system; callers do not need to understand its contents here. The file takes raw payload bytes, turns them into URL-safe text, and attaches a cryptographic signature made with HMAC-SHA256. HMAC is a standard way to prove that someone who knows a secret key created or approved a message, without revealing the secret itself.

A token made here looks like two text parts separated by a dot: the encoded payload, then the signature. This is like sealing an envelope with a wax stamp: anyone can carry the envelope, but if the stamp does not match, the receiver knows not to trust it.

On the checking side, the file first makes sure the token has the expected shape. Then it recomputes the signature from the payload text and compares it carefully using a timing-safe comparison, which avoids leaking clues through tiny timing differences. If the signature is valid, it decodes the payload back into bytes. If anything is wrong, it raises `SignedTokenError`, a clear error type meaning “this token cannot be trusted or read.” Without this file, other code would have to repeat delicate signing and verification logic, increasing the risk of subtle security mistakes.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: Creates a standalone signature for a message using a secret key. Someone would use this when they need proof that a specific byte message was approved by whoever knows the secret.

**Data flow**: It receives a secret as bytes and a message as bytes. It feeds both into HMAC-SHA256 to produce a fixed-size digest, then turns that digest into URL-safe base64 text and removes padding characters. It returns that signature string and does not change anything else.

**Call relations**: This is the low-level stamping tool. `sign_token` uses it to attach a signature to a token body, and `verify_detached` uses it to recreate the expected signature before comparing it with one it was given.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: Checks whether a supplied signature really matches a message and secret. It answers yes or no without decoding or changing the message.

**Data flow**: It receives the secret, the original message bytes, and a signature string. It recomputes what the signature should be by calling `sign_detached`, then compares the expected and supplied signatures using a safe comparison designed for secrets. It returns `true` if they match and `false` otherwise.

**Call relations**: This is the checker used by `verify_token`. When a full token is being verified, `verify_token` separates the body from the signature and asks this function whether the signature can be trusted.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Builds a complete signed token from raw payload bytes. Callers use it when they want to send or store data in a compact text form that can later be checked for tampering.

**Data flow**: It receives a secret and a byte payload. First it converts the payload into URL-safe base64 text and removes padding. Then it signs that encoded body text with `sign_detached`. Finally it joins the body and signature with a dot and returns the complete token string.

**Call relations**: This is the outward-facing token maker in the file. It relies on `sign_detached` for the cryptographic proof, while it takes care of the token shape: encoded payload, dot, signature.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks a complete signed token and returns the original payload bytes if it is valid. It raises `SignedTokenError` when the token is missing pieces, has the wrong signature, or contains unreadable payload text.

**Data flow**: It receives a token string and the secret. It splits the token at the dot into a body and signature. If either part is missing, it stops with an error. It then asks `verify_detached` whether the signature matches the body. If the signature is valid, it restores any needed base64 padding and decodes the body back into the original bytes. The output is the trusted payload bytes, or an exception if the token cannot be trusted or read.

**Call relations**: This is the outward-facing token reader in the file. It calls `verify_detached` to decide whether the token is authentic, then uses base64 decoding to recover the payload. It is the counterpart to `sign_token`: one creates the sealed envelope, the other checks the seal and opens it.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### Sandbox Ingress Access
Sandbox ingress tokens apply signed, time-limited claims to restrict browser access to a specific sandbox port.

### `core/src/ufo/sandbox/ingress_token.py`

`domain_logic` · `minting ingress links and request-time token verification`

A sandbox may expose a web page on a port, but the system cannot simply leave that port open to anyone. This file works like a temporary, tamper-proof ticket booth. It issues tickets that say: which workspace, which conversation, which port, what kind of visit, and when the ticket expires. The ticket is signed with a deploy-wide secret, so if someone changes even one field, verification fails.

There are two different ticket kinds. A “view” token is placed in the first link opened by the frame. A “session” token is later stored as that origin’s cookie. The kind is part of the signed data, so a cookie cannot be used as if it were the first view link, and a view link cannot be pasted in as a session cookie. This separation prevents one step of the flow from impersonating the other.

The file also supports two optional claims. A shipped claim points to a prebuilt app bundle served from shared storage. A framer claim names a related enclosing site that may be allowed to frame the page. Verification is deliberately strict: bad signatures, malformed data, expired tokens, wrong token kinds, and invalid port numbers all fail with the same ingress-token error.

#### Function details

##### `mint_ingress_token`  (lines 76–93)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns trusted ingress claims into a signed token string. Someone uses it when they want to create a browser link or cookie that grants access to exactly one sandbox hop.

**Data flow**: It receives an IngressClaims object and a token kind, such as a view token or a session token. It copies the workspace, conversation, port, expiry time, and any optional shipped or framer information into a JSON body, adds the token kind, reads the shared ingress secret, and signs the JSON. The result is an opaque string that can be handed to a browser but cannot be safely edited by the browser.

**Call relations**: When the system needs to issue an ingress ticket, this function builds the signed body. It calls ingress_secret to get the deploy-wide secret, uses JSON encoding to make a stable payload, and hands that payload to the token-signing helper. Later, verify_ingress_token reads the same kind of body back and checks that it is still trustworthy.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 96–135)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether a token is genuine, still alive, meant for the current hop, and safe to use. If all checks pass, it returns the claims the ingress server may trust.

**Data flow**: It receives a token string, the current time, and the kind of token the caller expects. It reads the shared ingress secret, verifies the signature, parses the JSON payload, checks that the token kind matches, rebuilds typed claim objects from the payload, rejects impossible port numbers, and compares the expiry time against now. If anything is wrong, it raises IngressTokenError; if everything is right, it returns an IngressClaims object ready for the ingress code to use.

**Call relations**: This is the gatekeeper on the receiving side of the flow. It uses ingress_secret just like mint_ingress_token, so both sides depend on the same deploy secret. It delegates signature checking to the shared token verification helper, then creates ShippedClaim, FramerClaim, and IngressClaims objects only after the payload has the expected shape. Callers use it when serving an ingress request and must state whether they are accepting a view token or a session token.

*Call graph*: calls 1 internal fn (ingress_secret); 8 external calls (__init__, __init__, __init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 138–145)

```
def ingress_secret() -> str
```

**Purpose**: This function fetches the shared secret used to sign and verify ingress tokens. It makes missing configuration fail loudly instead of letting the service appear healthy while every viewer request would fail.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value is present, it returns that secret string. If the value is missing or empty, it raises a RuntimeError explaining that the secret must be configured before tokens can be minted or verified.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this function before doing any signing or signature checking. That makes the secret source consistent across issuing and accepting tokens, and it centralizes the configuration check in one place.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).

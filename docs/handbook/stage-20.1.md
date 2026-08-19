# Signed tokens, sessions, and protected links  `stage-20.1`

This stage is shared behind-the-scenes security support. It gives the system a way to hand out small pieces of text, called tokens, that can be checked later without keeping a database record for each one. A token is like a sealed note: people can carry it around, but if they change it, the seal no longer matches.

The common sealing tool lives in token_signing.py. It signs token contents with a secret key and later checks that the text was really made by the system and was not altered. The other files use that tool for specific jobs. bearer.py makes member login tokens, so services can recognize a logged-in member without storing a server-side session. artifact_url.py makes time-limited download links for allowed artifact files and rejects forged or expired links. ingress_token.py makes short-lived passes for reaching one sandbox port through the browser. surface_token.py makes permanent signed identifiers for surfaces, carrying safe text claims. Together, these pieces protect identity, downloads, and access links with one consistent signing method.

## Files in this stage

### Artifact download links
Creates and validates signed, expiring links for authorized artifact downloads.

### `core/src/ufo/artifact_url.py`

`domain_logic` · `artifact sharing and download request handling`

This file is the security gate for artifact downloads. An artifact is a stored file, and its download link is not just a path to bytes. It also carries a signed grant in the query string, like a sealed permission slip. The signature is made with a deployment secret, so if someone changes the artifact id, expiry time, workspace, or preview claim, verification fails and no file is served.

The file also keeps artifact paths inside a narrow safe shape: `artifacts/<artifact-id>/<filename>`. That prevents a link from being used to reach unrelated storage keys. The filename itself is not signed, but it must still be a simple filename, not a path. If it is changed, it points to a different blob under the same artifact id and normally finds nothing.

There is special support for image previews. A link may say that a raster image can be shown inline, but only if the claim says the exact media type and byte size, and only within configured preview limits.

Expired links are treated carefully. If the signature is still valid but the time has passed, the code raises a special error that keeps the proven claims. A signed-in workspace member can then refresh access, while anonymous access is blocked. Older links without a workspace claim are also forced through that refresh path instead of serving bytes directly.

#### Function details

##### `ArtifactUrlExpired.__init__`  (lines 70–72)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: This builds the special error used when a link is genuine but too old to use anonymously. It keeps the verified claims so another part of the system can decide whether a signed-in workspace member may refresh the link.

**Data flow**: It receives already-checked artifact claims. It turns them into an exception with the message `artifact url is expired`, and stores the claims on the exception for later use.

**Call relations**: When `verify_artifact_url` confirms that a URL was properly signed but is expired, or lacks the modern workspace claim, it creates this error instead of returning normal access. The download route can catch that error and use the attached claims during a member-only refresh flow.

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 75–87)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: This decides what content type should be used when serving an artifact file. A content type tells the browser what kind of file it is, such as a patch, spreadsheet, image, or generic download.

**Data flow**: It receives a filename. First it checks a built-in table for file types the product cares about, such as Office files and patch files. If there is no table match, it asks Python’s MIME type guesser, but only accepts the answer if the file is not also marked as compressed. If no safe type is found, it returns the generic fallback `application/octet-stream`.

**Call relations**: This function stands apart from the signing flow. It is used when artifact bytes are being served so the response can describe the file correctly, without depending only on the host machine’s MIME database, which may differ between development and production.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 90–113)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: This creates a signed download path for an artifact. It is used when the system wants to give someone a temporary link that can later prove it was issued by this deployment.

**Data flow**: It receives the secret signing key, a workspace-relative blob key, an expiry time, the owning workspace id, and optionally an image preview grant. It checks that the secret exists, splits and validates the artifact storage key, checks the preview claim if present, builds the exact message to sign, signs it, URL-escapes the filename and preview value where needed, and returns a path like `/artifacts/...?...` with the grant in the query string.

**Call relations**: This is the issuing side of the flow. It relies on `_split_key` to reject unsafe artifact addresses, `_parsed_preview` to make sure preview claims are well-formed, `_signed_message` to build the bytes that must be protected, and `sign_detached` to produce the tamper-proof signature. Later, `verify_artifact_url` checks the same message shape.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); 3 external calls (__init__, sign_detached, quote).


##### `verify_artifact_url`  (lines 116–157)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: This checks whether an artifact download URL is valid and still usable. It protects the storage system from forged, malformed, expired, or out-of-scope artifact links.

**Data flow**: It receives the signing secret, the artifact id and filename from the path, the expiry, signature, preview value, workspace value from the query string, and the current time. It validates the shapes of the id, filename, expiry, workspace, and preview claim. It rebuilds the signed message and checks the signature. If everything matches, it creates `ArtifactClaims` describing what the link proves. If the link has no workspace claim or is past its expiry time, it raises `ArtifactUrlExpired` with those claims; otherwise it returns the claims for serving the file.

**Call relations**: This is the checking side of the flow, usually reached by the artifact download route. It mirrors `mint_artifact_url` by using `_signed_message` and `_parsed_preview`, and it uses `_is_canonical_uuid` and `_is_filename` to reject unsafe input before any file is served. When access cannot continue anonymously because of expiry or old link format, it hands control to the refresh path through `ArtifactUrlExpired`.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 160–162)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: This builds the exact text that is signed when a URL is minted and checked when a URL is verified. Both sides must use the same recipe, or valid links would fail.

**Data flow**: It receives the workspace value, artifact id, expiry value, and preview value as strings. If a workspace is present, it prefixes the message with it; then it joins the important pieces with colons and returns the result as bytes ready for signing or verification.

**Call relations**: Both `mint_artifact_url` and `verify_artifact_url` call this helper. It is the shared agreement about what the signature protects, like writing the same sentence on both the original permission slip and the later inspection checklist.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 165–176)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: This checks and converts the optional image preview claim. A preview claim says an image may be shown inline, but only for allowed raster image types and only below the configured byte limit.

**Data flow**: It receives a preview string shaped like `media/type:size`. It splits the string at the last colon, checks that the media type is one of the allowed raster image types, checks that the size is a number, and rejects sizes above the preview maximum. If valid, it returns an `ImagePreviewGrant`; if not, it returns `None`.

**Call relations**: When minting a URL, `mint_artifact_url` uses this to reject preview claims it would not later understand. When checking a URL, `verify_artifact_url` uses it to turn the signed preview text back into a trusted grant.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 179–188)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: This validates and breaks apart an artifact storage key before a URL is minted. It makes sure the system only signs links for the artifact namespace, not arbitrary storage paths.

**Data flow**: It receives a blob key such as `artifacts/<uuid>/<filename>`. It removes the artifact prefix, separates the artifact id from the filename, checks that the prefix is present, the id is a standard UUID string, and the filename is a simple single path segment. If valid, it returns the artifact id and filename; otherwise it raises an artifact URL error.

**Call relations**: `mint_artifact_url` calls this before signing anything. It uses `_is_canonical_uuid` and `_is_filename` as small safety checks so the minted URL cannot point outside the intended artifact layout.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 191–195)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: This answers whether a string is a normal, canonical UUID. A UUID is a standard identifier format, and this check avoids accepting odd alternate spellings.

**Data flow**: It receives a string. It tries to parse it as a UUID and then compares the normalized UUID text back to the original. It returns `true` only when the input already matches that canonical form; invalid UUID text returns `false`.

**Call relations**: `_split_key` uses this when deciding whether a storage key is safe to sign. `verify_artifact_url` uses it when checking the artifact id and workspace id supplied by an incoming request.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 198–199)

```
def _is_filename(value: str) -> bool
```

**Purpose**: This checks that a value is just a filename, not a path. That keeps callers from sneaking in `/`, `.`, or `..` path tricks.

**Data flow**: It receives a string. It returns `true` only if the string is not empty, contains no slash, and is not exactly `.` or `..`; otherwise it returns `false`.

**Call relations**: `_split_key` uses this before minting a signed URL, and `verify_artifact_url` uses it before accepting the filename from a download request. Together, those checks keep artifact links focused on one file under one artifact id.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### Member login sessions
Defines stateless signed bearer tokens for UFO member login and session identity.

### `core/src/ufo/bearer.py`

`domain_logic` · `token minting and request authentication`

This file is the shared rulebook for UFO bearer tokens, which are short strings that prove a user belongs to a workspace. A bearer token is like a signed wristband at an event: anyone who knows how to check the signature can trust what it says, but outsiders cannot make a fake one without the secret key.

The token contains three claims: the workspace ID, the member email, and an expiry time. These claims are turned into compact JSON, encoded in URL-safe base64, and signed with HMAC-SHA256. HMAC is a way to make a tamper-evident signature using a shared secret. If someone changes even one character in the token, the signature check fails.

The important design choice is that the server does not need to remember issued tokens. Verification recomputes the expected signature using `UFO_TOKEN_SECRET`, checks it in a timing-safe way, decodes the payload, confirms the fields have the right shape, and rejects expired tokens. Higher-level helpers then either confirm the token belongs to one specific workspace or extract the workspace claim for shared services. The file also defines the browser cookie name and login path used by surfaces that carry this token.

#### Function details

##### `mint_token`  (lines 34–51)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a new signed bearer token for one workspace and one email address. It is used when the system wants to give a member proof they can later present without the server storing a session row.

**Data flow**: It receives a secret key, a workspace ID, an email address, a time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates the expiry time, packs the workspace, email, and expiry into compact JSON, base64-encodes that JSON, signs the encoded body with the secret, and returns one string made from the body plus the signature.

**Call relations**: This is the issuing side of the same format that `verified_claims` later checks. Token minters call it when they need to produce a token that UFO surfaces can accept later.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 54–77)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the workspace and email it proves. If anything looks wrong, it returns nothing instead of trusting partial information.

**Data flow**: It takes a token string and optionally a current timestamp. It reads the shared signing secret from the environment, splits the token into body and signature, recomputes the signature, compares it safely, decodes the JSON payload, checks that the expected fields exist with the right types, and confirms the expiry time is still in the future. On success it returns the workspace string and email string; on failure it returns `None`.

**Call relations**: `verify_token` and `workspace_claim` both rely on this as the first gate. It hands them only claims that have already passed signature and expiry checks, so they can add their own workspace-specific rules.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 80–91)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Confirms that a token authenticates a member for one specific workspace. It is useful when a running service is pinned to a known workspace and must reject tokens from any other workspace.

**Data flow**: It receives a token, the expected workspace UUID, and optionally a current timestamp. It asks `verified_claims` to validate the token, compares the token's workspace claim with the expected workspace, and returns the member email in lowercase if they match. If verification fails or the workspace is different, it returns `None`.

**Call relations**: This function sits one step after `verified_claims`. It adds the tenant check: even a real token is not accepted unless it belongs to the workspace this caller is protecting.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 94–105)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the verified workspace ID from a token. It is meant for shared services that serve many workspaces and need to decide which workspace a request belongs to.

**Data flow**: It receives a token and optionally a current timestamp. It first asks `verified_claims` to prove the token is signed and unexpired, then tries to turn the workspace claim into a real UUID object. It returns that UUID on success, or `None` if the token is invalid or the workspace text is not a valid UUID.

**Call relations**: Like `verify_token`, it builds on `verified_claims`, but instead of comparing against a known workspace it returns the workspace for later request scoping.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 108–112)

```
def _secret() -> str
```

**Purpose**: Reads the token-signing secret from the environment. This keeps the secret out of callers' hands during verification.

**Data flow**: It looks up `UFO_TOKEN_SECRET` in the process environment. If the value exists, it returns it; if it is missing or empty, it raises an error because tokens cannot be safely verified without the secret.

**Call relations**: `verified_claims` calls this before checking a token signature. That means every verification uses the deployment's configured secret, not a secret passed in by an untrusted caller.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 115–116)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the compact base64 text used inside the token body. It hides the small padding detail needed to reverse the encoding used when tokens are minted.

**Data flow**: It receives a base64url string that may be missing padding characters. It adds the required padding back, decodes the text into bytes, and returns those bytes for JSON parsing.

**Call relations**: `verified_claims` uses this after the signature has passed, so the token body can be turned back into the original JSON claims.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### Sandbox ingress access
Issues and verifies short-lived signed tokens for access to specific sandbox ports.

### `core/src/ufo/sandbox/ingress_token.py`

`domain_logic` · `request handling and startup validation`

This file is about controlled access to sandbox web ports. A sandbox may expose a local port, but the system does not want anyone with a guessed URL to reach it. So it uses an ingress token: a small signed message that says, in effect, “this workspace and conversation may access this exact port until this time.”

The file defines two token kinds because a visit happens in two steps. A “view” token is placed in the first link a browser opens. After that, the ingress can mint or bind a separate “session” token, usually as a cookie. The kind is included inside the signed token, so the two cannot be swapped. This is like having one ticket for entering the building and a different badge for staying inside; one cannot be reused as the other.

The claims are stored in `IngressClaims`: workspace ID, conversation ID, port, and expiry time. `mint_ingress_token` turns those claims into JSON and signs them with the deployment secret. `verify_ingress_token` checks the signature, checks the token kind, rebuilds the claims, refuses impossible ports, and rejects expired tokens. `ingress_secret` reads the shared secret from the environment and fails loudly if the deployment forgot to provide it.

#### Function details

##### `mint_ingress_token`  (lines 50–62)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: Creates a signed token for one sandbox ingress step. Someone uses it when they need to give a browser temporary permission to reach one workspace conversation on one port.

**Data flow**: It receives trusted `IngressClaims` and a token kind, such as a view token or session token. It turns the claims into a JSON message, reads the shared ingress secret, and signs the message so later tampering can be detected. It returns the signed token as a string, ready to put in a URL or cookie.

**Call relations**: This is the issuing side of the token flow. Before signing, it asks `ingress_secret` for the deployment secret, then hands the prepared JSON bytes to the shared token-signing helper. Later, `verify_ingress_token` is expected to read the same signed body back and confirm it.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 65–87)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: Checks whether a presented ingress token is genuine, still alive, meant for the expected step, and limited to a valid port. It returns the permission details only if all checks pass.

**Data flow**: It receives a token string, the current time, and the kind of token the caller expects. It reads the shared secret, verifies the signature, parses the JSON, checks that the token kind matches, converts the IDs and numbers into proper values, rejects ports outside the normal TCP port range, and compares the expiry time with `now`. If everything is good, it returns an `IngressClaims` object; otherwise it raises `IngressTokenError`.

**Call relations**: This is the checking side of the flow, used when ingress receives a browser request or session cookie. It depends on `ingress_secret` for the same secret used at minting time, uses the shared token verification helper to detect forged or changed data, and only then builds `IngressClaims` for the caller to use.

*Call graph*: calls 1 internal fn (ingress_secret); 6 external calls (__init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 90–97)

```
def ingress_secret() -> str
```

**Purpose**: Fetches the shared deployment secret used to sign and verify ingress tokens. It exists so both token creation and token checking use the same configured secret.

**Data flow**: It reads the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the value is present, it returns that secret string. If it is missing or empty, it raises a runtime error, turning a bad deployment setup into an immediate and clear failure.

**Call relations**: Both `mint_ingress_token` and `verify_ingress_token` call this before doing cryptographic signing or checking. The design also lets the ingress process call it at boot, so a missing secret is discovered early instead of after the service appears healthy but fails every viewer request.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### Surface identity tokens
Defines permanent tamper-proof surface tokens carrying URL-safe identity claims.

### `core/src/ufo/surface_token.py`

`domain_logic` · `link creation and request handling`

A “surface” can be reached from a link before the system has a cookie or workspace already selected. This file gives that link a safe way to carry the small pieces of information needed to find the right place, such as a workspace name or other route claim. Think of it like a sealed envelope in a URL: anyone can carry it, but only the server can make or verify the seal.

The file uses a shared secret from the environment to sign the token with an HMAC, which is a cryptographic stamp that proves the token body has not been changed. The token body always includes the name of the surface that minted it. When the token is later checked, the requested surface name must match the one inside the token. That prevents a token made for one surface from being reused as an address for another surface.

This is deliberately not an access-control system. Tokens do not expire here, and a valid token does not automatically authorize the request. It only says, “these are the claims this surface put in the URL, and they were not forged.” The actual route still has to apply its normal security checks afterward.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: This function makes a signed token for one surface. It is used when the system needs to place trustworthy route information into a URL without exposing the signing secret.

**Data flow**: It receives a surface name and a mapping of string claims. It rejects an empty surface name, and it also rejects any payload that tries to set the reserved surface field itself. It then builds a compact JSON body containing the surface name plus the supplied claims, reads the secret, signs the body, and returns the resulting token string.

**Call relations**: When a surface needs a permanent address token, this function gathers the claims, asks _secret for the shared signing secret, and passes the encoded body to sign_token. The result is handed back to whoever is building the link.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: This function checks whether a token is valid for a specific surface and, if so, returns the claims inside it. It is used when a request arrives with only a link token to tell the route what it should resolve.

**Data flow**: It receives the expected surface name and the token string from the request. It reads the secret, verifies the token’s cryptographic signature, parses the token body as JSON, and checks that the embedded surface name matches the expected one. If anything is wrong, it returns None. If everything is valid and all remaining claims are strings, it returns those claims without the reserved surface field.

**Call relations**: During request handling, this function is the gatekeeper for address claims. It calls _secret to get the same shared secret used when minting, then asks verify_token to prove the token was not forged or altered. Only after that does it parse and return claims for the route to use.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: This small helper fetches the shared token-signing secret from the process environment. It keeps minting and verification using the same configured secret.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value exists, it returns that string. If it is missing or empty, it raises an error because tokens cannot be safely signed or checked without the secret.

**Call relations**: Both mint_surface_token and verify_surface_token call this helper before using the lower-level token signing tools. It is the one place in this file that knows where the signing secret comes from.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### Shared signing primitive
Provides the common signing and verification machinery used by the concrete token formats.

### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file solves a common problem: sending a compact piece of data through places that only accept text, while making sure nobody can quietly edit it. The token is “opaque” because the code treats the payload as raw bytes, not as a structured message it understands. It is not encrypted, so someone may still decode the payload, but it is signed so changes can be detected.

The file uses HMAC, which is a way to make a fingerprint from a secret key and a message. Think of it like sealing an envelope with a wax stamp that only someone with the right stamp can recreate. The payload is first converted to base64url, a web-safe text form of bytes. Then the file adds a dot and a signature of that text body, producing a token like “body.signature”.

When checking a token, the file splits it at the dot, recomputes the signature, and compares it safely. If the token is missing pieces, has the wrong signature, or contains unreadable base64 data, it raises SignedTokenError. This gives the rest of the system one clear failure signal for bad or suspicious tokens.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: Creates a standalone signature for a byte message using a secret key. This is useful when the caller wants the proof of authenticity separate from the original message.

**Data flow**: It receives a secret key and a message, both as bytes. It uses HMAC with SHA-256 to make a binary fingerprint, then turns that fingerprint into short URL-safe text with padding removed. It returns that text signature and does not change anything else.

**Call relations**: This is the basic stamping step used by higher-level helpers. sign_token calls it when building a full token, and verify_detached calls it to recreate the expected signature before comparing.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: Checks whether a given signature really matches a message and secret key. Someone would use it to answer, “Was this message stamped by someone who knows the secret?”

**Data flow**: It receives the secret key, the message, and the signature text to test. It makes a fresh expected signature for the same message, then compares the two using a careful comparison method designed not to leak timing clues. It returns true if they match and false if they do not.

**Call relations**: This function sits between raw signing and full token verification. verify_token calls it after splitting a token into its body and signature, and it relies on sign_detached to recreate the signature that should be present.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Builds a complete signed token from raw payload bytes. It packages the payload into web-safe text and attaches a signature so later code can detect tampering.

**Data flow**: It receives a secret key and payload bytes. It base64url-encodes the payload into the token body, signs that body text, and joins the body and signature with a dot. It returns the finished token string.

**Call relations**: This is the outward-facing token creation helper in the file. It hands the actual signature work to sign_detached, so token creation and signature creation stay consistent.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks a full signed token and returns the original payload if the token is valid. It rejects malformed, altered, or unreadable tokens with a SignedTokenError.

**Data flow**: It receives token text and a secret key. It splits the token into body and signature, checks that both parts exist, verifies the signature against the body, and then decodes the body back into the original bytes. On success it returns the payload bytes; on failure it raises a clear token-signing error.

**Call relations**: This is the main reading path for tokens made by sign_token. It calls verify_detached to confirm the signature before it trusts the body, then uses base64 decoding to recover the payload only after the authenticity check has passed.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).

# Signed tokens for routing, artifact access, and sandbox ingress  `stage-18.2`

This stage is shared behind-the-scenes support for links and routes that must not rely on whatever a client says. It is used when the system needs to hand out a small, temporary “proof ticket” in a URL: for routing to the right workspace, downloading an artifact, or opening access to a sandbox port.

At the center is token_signing.py. It makes compact signed tokens, which are small data bundles with a seal. A shared secret key creates the seal, and later checks whether the contents were changed. Other files build safer, purpose-specific tickets on top of that. surface_token.py signs early routing information for public surface routes, before normal login cookies or sessions are available. artifact_url.py creates short-lived download links tied to one exact artifact and workspace, and rejects expired or altered links. ingress_token.py does the same for sandbox access, but narrows the permission to one workspace conversation and one port. Together, these files let the system trust the token, not mutable URL input.

## Files in this stage

### Specialized access tokens
Short-lived signed tokens authorize concrete resource access for artifact downloads and sandbox port ingress.

### `core/src/ufo/artifact_url.py`

`domain_logic` · `artifact link creation and download request handling`

This file is the gatekeeper for artifact downloads. An artifact is a stored file, such as something produced or shared by the system. The system wants people to download these files through links, but not through links that can be guessed, edited, or reused forever. This file solves that by putting a signed grant in the URL, like a sealed permission slip with an expiry time.

When the system creates a link, it starts from a storage key such as `artifacts/<artifact-id>/<filename>`. It checks that the key is really inside the artifact area, signs the important claims with a secret key, and returns a URL path plus query string. The signature is an HMAC-style proof: anyone with the URL can present it, but only the server that knows the secret can make or verify it.

When a request comes back, the verifier checks the artifact id, filename, workspace id, expiry time, optional image preview claim, and signature. If anything was edited, the link is rejected. If the link is authentic but expired, the code raises a special error that still carries the verified claims, so another part of the system can refresh access for a signed-in workspace member.

The file also decides the media type, meaning the browser-facing file type, for artifact names. It uses known safe mappings for important extensions and falls back carefully when the host machine cannot guess a type.

#### Function details

##### `ArtifactUrlExpired.__init__`  (lines 70–72)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: This creates the special error used when an artifact URL is genuine but no longer valid because its expiry time has passed. It keeps the verified claims attached, so trusted code can decide whether a logged-in user is allowed to refresh the link.

**Data flow**: It receives already-verified artifact claims. It turns them into an exception with the message “artifact url is expired” and stores the claims on that exception. Nothing is returned because exceptions are raised, not returned.

**Call relations**: The URL verifier calls this when a signature checks out but the URL is expired, or when an older URL has no workspace claim and must not be served directly. The raised object lets the download flow distinguish “fake link” from “real link that needs member-based refresh.”

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 75–87)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: This chooses the file type that should be reported when serving an artifact. Browsers and clients use this type to decide whether to display, download, or process the file.

**Data flow**: It takes a filename. First it looks at the filename suffix, such as `.docx` or `.patch`, in the project’s own trusted table. If there is no match, it asks Python’s MIME type guesser, but only accepts the guess when there is no separate compression encoding like gzip. It returns a media type string, or a safe generic fallback when the type is unknown.

**Call relations**: This function stands apart from signing and verification. Other artifact-serving code can call it when it has a filename and needs to send a correct `Content-Type` value. It uses Python’s path and MIME helpers, but avoids depending entirely on host-specific MIME databases.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 90–113)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: This creates a signed, expiring artifact download URL. It is used when the system wants to give someone temporary access to a specific stored artifact file.

**Data flow**: It receives the signing secret, the artifact storage key, an expiry timestamp, the workspace id, and optionally an image preview permission. It checks that a secret exists, splits and validates the storage key, turns the preview permission into a signed text claim if present, signs the important values, URL-escapes the filename and preview text, and returns a relative URL such as `/artifacts/...?...`. If the key or preview is invalid, it raises an artifact URL error instead of making a link.

**Call relations**: This is the creation side of the pair with `verify_artifact_url`. It relies on `_split_key` to prove the blob key is a safe artifact address, `_parsed_preview` to check preview claims, `_signed_message` to build the exact bytes that are signed, and `sign_detached` to create the tamper-proof signature. Later, the artifact route verifies the same values before serving bytes.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); 3 external calls (__init__, sign_detached, quote).


##### `verify_artifact_url`  (lines 116–157)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: This checks whether an incoming artifact download URL is authentic, still valid, and safe to serve. It turns URL pieces into trusted claims only after all checks pass.

**Data flow**: It receives the secret, URL path pieces, query-string values, and the current time. It first rejects missing configuration, malformed UUIDs, unsafe filenames, bad expiry values, and invalid preview claims. Then it rebuilds the signed message and verifies the signature. If the signature matches, it creates `ArtifactClaims` containing the workspace id, blob key, filename, expiry, and preview grant. If the URL has no workspace claim or is past its expiry, it raises `ArtifactUrlExpired` with those claims. Otherwise it returns the trusted claims for the caller to use when serving the file.

**Call relations**: This is the checking side of the pair with `mint_artifact_url`. Download request code calls it before reading artifact bytes. It uses `_is_canonical_uuid`, `_is_filename`, `_parsed_preview`, and `_signed_message` to rebuild exactly what should have been signed. It calls `ArtifactUrlExpired.__init__` when the link is real but cannot be served directly.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 160–162)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: This builds the exact text that is signed when a URL is created and checked when a URL is verified. Having one helper for both sides prevents the creator and verifier from disagreeing about what the signature protects.

**Data flow**: It receives the workspace id text, artifact id, expiry text, and preview claim text. If there is a workspace id, it prefixes the message with it; otherwise it leaves that part out for older claim-less links. It joins the fields with colons and returns the result as bytes, ready for the signing code.

**Call relations**: `mint_artifact_url` calls this before creating a signature, and `verify_artifact_url` calls it before checking a signature. It is the shared recipe that makes signed links round-trip correctly.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 165–176)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: This checks and converts an optional image preview claim. A preview claim says that a raster image, meaning a pixel-based image such as PNG or JPEG, may be shown inline and states its exact media type and size.

**Data flow**: It receives a text value shaped like `media/type:size`. It splits the text at the final colon, checks that the media type is one of the allowed raster image types, checks that the size is a number, and checks that the size is not above the preview limit. If all checks pass, it returns an `ImagePreviewGrant`; otherwise it returns `None`.

**Call relations**: `mint_artifact_url` uses this to refuse creating a URL with a bad preview claim. `verify_artifact_url` uses it to reject tampered or malformed preview claims before trusting them. The result becomes part of the verified artifact claims.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 179–188)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: This validates and splits a storage key into the artifact id and filename parts needed for a signed URL. It prevents callers from minting links to anything outside the artifact storage area.

**Data flow**: It receives a blob key string. It removes the expected `artifacts/` prefix, separates the artifact id from the filename, and checks that the prefix exists, the separator exists, the artifact id is a canonical UUID, and the filename is safe. If everything is valid, it returns the artifact id and filename. If not, it raises an artifact URL error.

**Call relations**: `mint_artifact_url` calls this before signing any URL. It delegates the UUID check to `_is_canonical_uuid` and the filename check to `_is_filename`, so unsafe storage paths are stopped before a downloadable link can be made.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 191–195)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: This answers whether a string is a UUID written in the normal canonical form. A UUID is a standard unique identifier, and using one strict spelling avoids accepting lookalike or unusual forms.

**Data flow**: It receives a string. It tries to parse it as a UUID, then converts it back to the standard string form and compares that to the original. It returns `true` only when the value parses and already matches the canonical spelling; otherwise it returns `false`.

**Call relations**: `_split_key` uses this when validating artifact ids in storage keys. `verify_artifact_url` uses it when checking artifact ids and workspace ids from incoming URLs before trusting or signing-related comparisons happen.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 198–199)

```
def _is_filename(value: str) -> bool
```

**Purpose**: This checks that a filename is just a single safe filename, not a path. It blocks empty names, nested paths, and special directory names like `.` and `..`.

**Data flow**: It receives a string and returns `true` only if the value is non-empty, contains no slash, and is not `.` or `..`. It does not change anything.

**Call relations**: `_split_key` uses this before a URL is minted from a blob key. `verify_artifact_url` uses it when a filename comes back from a request, so an edited URL cannot turn the filename part into a path traversal attempt.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### `core/src/ufo/sandbox/ingress_token.py`

`domain_logic` · `request handling and ingress startup checks`

Sandbox ingress is the doorway from a browser or client into a running sandbox service. This file is the ticket system for that doorway. A token says, in effect: “this visitor may reach this one port for this one workspace conversation until this time.” The token is signed with a deployment secret, which is a shared private value from the environment. Signing is like sealing an envelope with a tamper-evident stamp: anyone can carry the token, but changing its contents breaks the seal.

There are two kinds of token. A “view” token is used when opening the ingress view link. A “session” token is used later as the browser session proof, such as in a cookie. The kind is included inside the signed data, so the two cannot be swapped. This matters because a link token should not be reusable as a session cookie, and a session cookie should not be able to reopen the view flow.

The file also checks practical safety rules after the signature is valid: the workspace and conversation IDs must be real UUIDs, the port must be in the usable TCP port range, and the expiry time must still be in the future. If anything is wrong, callers get a single ingress-specific error instead of partly trusted data.

#### Function details

##### `mint_ingress_token`  (lines 50–62)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns trusted ingress claims into a signed token string that can be safely handed to a browser or client. The caller chooses whether the token is for the initial view step or the later session step, and that choice is locked into the signed data.

**Data flow**: It receives an IngressClaims object containing the workspace ID, conversation ID, allowed port, and expiry time, plus the token kind. It converts those fields into JSON text, reads the shared ingress secret, and signs the JSON. The output is a token string; the input claims are not changed.

**Call relations**: When another part of the system needs to issue ingress access, it calls this function rather than building a token by hand. This function asks ingress_secret for the deployment secret, then hands the prepared payload to the shared signing helper so verification later can detect tampering.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 65–87)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether a presented ingress token is genuine, still valid, meant for the expected step, and safe to use. If all checks pass, it returns the precise access the token grants.

**Data flow**: It receives a token string, the current time, and the expected token kind. It reads the shared secret, verifies the token signature, parses the JSON inside, checks that the kind matches, turns the workspace and conversation values into UUIDs, converts the port and expiry into numbers, rejects invalid ports, and rejects expired tokens. The output is an IngressClaims object for a valid token; otherwise it raises IngressTokenError and returns no access.

**Call relations**: Ingress request code uses this function before trusting a browser link or session cookie. It relies on ingress_secret to use the same deployment secret as mint_ingress_token, calls the shared token verification helper to prove the seal is intact, and only then constructs IngressClaims for the rest of the ingress flow to use.

*Call graph*: calls 1 internal fn (ingress_secret); 6 external calls (__init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 90–97)

```
def ingress_secret() -> str
```

**Purpose**: This function fetches the deployment secret used to sign and verify ingress tokens. It fails loudly if the secret is missing, because running ingress without it would make token access impossible and unsafe.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the variable contains a value, it returns that string. If it is empty or missing, it raises a runtime error explaining that the secret must be set.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this function so they use exactly the same secret source. The comment notes that ingress can also call it during startup, which catches a bad deployment before the service appears healthy but then fails every viewer request.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### Surface routing claims
Public surface links carry trusted workspace-like routing claims before normal session state is available.

### `core/src/ufo/surface_token.py`

`domain_logic` · `request handling`

Some routes are reached by a plain link, so the server cannot rely on a browser cookie to know what workspace or context the request belongs to. This file solves that by putting the needed claims into a signed token. A signed token is like a sealed envelope: anyone can carry it in a URL, but if someone changes the contents, the seal no longer matches.

The token is not treated as permission to do anything by itself. It is only an address: it says which surface made it and carries string claims that help the route find the right place. The route still has to run its normal access checks afterward.

The file uses a shared secret from the environment variable named by `UFO_TOKEN_SECRET_ENV`. `mint_surface_token` builds a small JSON payload, adds the surface name under a reserved key, and signs it. `verify_surface_token` checks the signature, checks that the token was meant for the surface currently receiving it, and returns only the user-provided string claims. If the token is forged, malformed, meant for another surface, or contains non-string claims, verification returns `None` instead of trusting it. This keeps one surface’s links from being reused as valid addresses for another surface.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: This function creates a signed surface token for use in a URL. It is used when a surface needs to hand out a durable address that carries routing claims, such as which workspace should be selected, without letting the caller edit those claims unnoticed.

**Data flow**: It takes a surface name and a mapping of string claims. It first rejects an empty surface name and rejects any claim named `surface`, because that name is reserved for the file’s own safety check. It then builds a compact JSON body containing the surface name plus the claims, reads the signing secret through `_secret`, signs the body, and returns the token string.

**Call relations**: When token creation is needed, this function is the front door. It asks `_secret` for the shared secret, uses JSON encoding to make a stable byte representation of the claims, and hands that to `sign_token` so the result can later be checked by `verify_surface_token`.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: This function checks whether a token is genuine and belongs to the surface that is trying to use it. If everything checks out, it returns the claims the route can use to identify the right context; otherwise it returns `None`.

**Data flow**: It takes the expected surface name and a token string. It reads the shared secret through `_secret`, asks `verify_token` to confirm the token’s signature, and parses the verified bytes as JSON. It then checks that the payload is a dictionary, that its reserved `surface` value matches the expected surface, and that all remaining claim keys and values are strings. The output is a dictionary of claims with the reserved surface field removed, or `None` if any check fails.

**Call relations**: This is the counterpart to `mint_surface_token`. A route calls it when a request arrives with a token in the URL. It relies on `_secret` and `verify_token` before trusting any content, then uses JSON parsing and local validation to make sure only well-shaped claims reach the route logic.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: This helper fetches the shared signing secret from the process environment. It centralizes the rule that surface tokens cannot be created or checked unless the secret is configured.

**Data flow**: It reads the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the value exists, it returns that secret string. If it is missing or empty, it raises a runtime error explaining that the secret must be set for signing and verification.

**Call relations**: Both `mint_surface_token` and `verify_surface_token` call this before using the lower-level token signing tools. That means token creation and token checking always use the same configured secret source, matching the project’s bearer-token behavior.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### Shared signing primitive
A compact shared-secret token utility provides the tamper-detection foundation used by the specialized token mechanisms.

### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file solves a common trust problem: sometimes the system needs to give a client or another part of the program a small blob of data, then later accept it back and know it was not edited. The file does not encrypt the data, so the payload is not hidden. Instead, it signs it, like putting a tamper-evident seal on an envelope.

The signing method is HMAC, which is a standard way to make a short proof from a secret key and a message. Here it uses SHA-256, a widely used hashing algorithm. The payload is first turned into base64url text, which is a web-safe text form of bytes. Then the signature is calculated over that text body. The final token looks like two pieces joined by a dot: the readable body and its signature.

When a token comes back, the file splits it at the dot, checks that both parts are present, recomputes the expected signature, and compares it safely. If the signature is wrong, the token is rejected before the payload is decoded. If the body is not valid base64url text, it is also rejected. All token problems are reported with SignedTokenError, so callers can treat malformed, tampered, or unreadable tokens as one clear category of failure.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: This function makes a standalone signature for a message using a secret key. It is useful when the caller wants the proof of authenticity separate from the data itself.

**Data flow**: It receives a secret key as bytes and a message as bytes. It uses HMAC-SHA256 to make a binary digest, then turns that digest into base64url text without trailing padding characters. It returns that text signature and does not change anything else.

**Call relations**: sign_token calls this when building a full token, using it to seal the token body. verify_detached also calls it to recreate the expected signature before checking whether a supplied signature matches.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: This function checks whether a separate signature really belongs to a message and secret key. It answers yes or no without decoding or interpreting the message itself.

**Data flow**: It receives the secret key, the original message bytes, and a signature string to check. It recomputes the correct signature with sign_detached, then compares the two signatures using a timing-safe comparison, which avoids leaking clues through tiny timing differences. It returns true if they match and false otherwise.

**Call relations**: verify_token calls this after it has split a token into its body and signature. verify_detached hands the actual signing work to sign_detached, then performs the safe equality check needed before a token can be trusted.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: This function turns raw payload bytes into a complete signed token string. Callers use it when they want to store or send data in a compact form that can later be checked for tampering.

**Data flow**: It receives a secret key and payload bytes. It converts the payload into base64url text, signs that text body with sign_detached, and joins the body and signature with a dot. It returns the finished token string.

**Call relations**: This is the token-creation side of the flow. It relies on sign_detached for the cryptographic seal, and its output is designed to be accepted later by verify_token.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: This function checks a full signed token and returns the original payload bytes if the token is valid. It is the gatekeeper that rejects tokens that are missing parts, have been tampered with, or cannot be decoded.

**Data flow**: It receives a token string and the secret key. It splits the token into body and signature at the dot, rejects the token if either required part is missing, then asks verify_detached whether the signature matches the body. If the signature is valid, it decodes the base64url body back into bytes and returns those bytes. If anything is wrong, it raises SignedTokenError instead of returning data.

**Call relations**: This is the token-reading side of the flow. It calls verify_detached before decoding so that altered tokens are stopped early, then uses base64 decoding to recover the payload that sign_token originally packed into the token.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).

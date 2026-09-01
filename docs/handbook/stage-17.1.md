# Authentication and Signed Links  `stage-17.1` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for proving identity and building safe links. It is used whenever the system must let a browser, extension, or download request in without keeping a server-side session for every case. At the center, token_signing.py makes small signed tokens, meaning data bundled with a tamper-proof stamp. bearer.py uses that stamp for login tokens that prove a user belongs to a workspace, while sdk/bearer.py exposes the checking side safely to extensions. surface_token.py signs links for shareable “surface” routes before normal session details are known.

Sandbox access uses the same pattern. ingress_token.py creates short-lived permission slips for one sandbox app port, and ingress_url.py packs the public host, port, conversation identity, and token into the browser link. artifact_url.py does this for stored files, creating expiring download links tied to one artifact.

External sign-in is the human-facing part. runtime/surfaces/cli.py receives the provider’s return request and serves the logo, callback_page.py builds the result page, and anthropic_login.py connects Anthropic accounts only after Anthropic confirms the credential works.

## Files in this stage

### Sandbox ingress access
Temporary sandbox browser links are assembled with port, conversation, and short-lived signed ingress claims.

### `core/src/ufo/harness/sandbox/ingress_url.py`

`domain_logic` · `request handling`

A sandbox may run a web server on some internal port, but a browser needs a public URL to reach it. This file creates that URL in a controlled way. Think of it like printing a temporary visitor badge: the URL points to the right door, and the token proves the visitor is allowed in for a limited time.

The main function, `mint_ingress_view_url`, starts with the configured public ingress URL. If there is no public URL, it returns nothing, because there is no outside address to use. It then creates claims, which are pieces of information placed inside an access token: which workspace and conversation this is for, which port should be exposed, when the link expires, and optionally which shipped artifact it came from. It also checks whether this view is being opened from another sandbox page, so the system can record a safe “framer” relationship.

The function then builds a host label from the conversation ID and port, attaches it as a subdomain of the public ingress host, adds the fixed ingress path, appends the freshly minted token, and finally adds the requested entry path. The result is a single expiring URL that a browser can open.

The helper `_framer_claim` is careful: it only accepts a framing page if it is under the same ingress base host and has a valid sandbox-style subdomain.

#### Function details

##### `mint_ingress_view_url`  (lines 17–50)

```
def mint_ingress_view_url(public_url: str | None, workspace_id: UUID, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest:
```

**Purpose**: Creates the temporary public URL for viewing one sandbox port in a browser. Someone would use it when they need to give the frontend or a user a safe link into a workspace’s running web service.

**Data flow**: It receives the public ingress URL, workspace and conversation IDs, the sandbox port, the desired path inside the sandbox app, and optional information about a shipped artifact or framing page. If there is no public ingress URL, it returns `None`. Otherwise it splits the public URL into parts, builds optional shipped and framer details, creates an expiring ingress token, creates a subdomain label from the conversation ID and port, safely quotes the entry path, and returns the finished browser URL string.

**Call relations**: This is the file’s main outward-facing function. During URL creation it asks `_framer_claim` to decide whether the caller came from another valid sandbox page. It also relies on the ingress token code to mint the short-lived token, and on the ingress host code to produce the subdomain label that routes the browser to the correct sandbox port.

*Call graph*: calls 1 internal fn (_framer_claim); 7 external calls (__init__, __init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `_framer_claim`  (lines 53–73)

```
def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None
```

**Purpose**: Checks whether a `framed_from` URL points to another valid sandbox page under the same public ingress host. If it does, it turns that page’s subdomain into a small claim naming the conversation and port that framed this view.

**Data flow**: It receives the already-parsed base ingress URL and an optional `framed_from` URL. It parses the framing URL, compares its scheme, port, and host against the base ingress URL, and rejects it if it is missing, malformed, or outside the expected host. If the host looks right, it removes the base host suffix, parses the remaining site label into a conversation ID and port, and returns a `FramerClaim`. If any check fails, it returns `None`.

**Call relations**: `mint_ingress_view_url` calls this helper while assembling token claims. The helper does the cautious validation work before handing back a framer claim, so the main URL builder can include framing information only when it came from a trusted sandbox-style URL.

*Call graph*: called by 1 (mint_ingress_view_url); 3 external calls (__init__, parse_site_label, urlsplit).


### `core/src/ufo/harness/sandbox/ingress_token.py`

`domain_logic` · `request handling`

This file is about safely letting a browser visit a sandboxed service. A sandbox may expose a local port, but the system does not want anyone to reach any port just by guessing a URL. Instead, it uses a signed token, like a tamper-proof wristband at an event. The token says which workspace, conversation, and port the visitor may use, and when that permission ends.

There are two different token types. A "view" token is used in the first link that opens the sandbox frame. A "session" token is later used as the browser cookie for that sandbox origin. The type is included inside the signed data, so one kind cannot be reused as the other. This prevents a cookie from being replayed as a fresh view link, and prevents a view link from acting like an established session.

The file defines small data containers for the claims inside a token, including optional information about a shipped app bundle or a framing sibling site. It also defines the rules for accepting a token: the signature must match the deployment secret, the shape must be correct, the port must be a real TCP port number, the kind must be expected, and the expiry time must still be in the future.

#### Function details

##### `mint_ingress_token`  (lines 76–93)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns approved ingress claims into a signed token string. Someone uses it when they need to create a safe link or session marker for exactly one sandbox access hop.

**Data flow**: It receives an IngressClaims object and a token kind, such as view or session. It copies the important fields into a plain JSON body, adds optional shipped-bundle or framer information when present, reads the shared deployment secret, and signs the JSON bytes. The result is a string token that can later prove it was made by this deployment and has not been changed.

**Call relations**: When a part of the sandbox system needs to hand the browser a trusted permission slip, it calls this function. This function depends on ingress_secret to get the shared secret, then hands the prepared JSON body to sign_token so the token can be checked later by verify_ingress_token.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 96–135)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether an incoming token is genuine, still valid, and meant for the current step of the ingress flow. It returns the permissions inside the token only if all safety checks pass.

**Data flow**: It receives a token string, the current time, and the kind of token the caller expects. It reads the same deployment secret used to mint tokens, verifies the signature, parses the JSON, checks that the kind matches, rebuilds the claim objects, rejects invalid port numbers, and rejects expired tokens. On success it returns an IngressClaims object; on failure it raises IngressTokenError instead of returning unsafe data.

**Call relations**: The ingress request path calls this when a browser presents a token. It uses ingress_secret and verify_token to prove the token came from this deployment, then constructs ShippedClaim, FramerClaim, and IngressClaims objects from the payload. If anything looks forged, malformed, expired, or meant for the wrong hop, it stops the flow by raising IngressTokenError.

*Call graph*: calls 1 internal fn (ingress_secret); 8 external calls (__init__, __init__, __init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 138–145)

```
def ingress_secret() -> str
```

**Purpose**: This function reads the shared secret used to sign and verify ingress tokens. It makes missing configuration fail loudly, because token security depends on every process using the same secret.

**Data flow**: It looks in the process environment for the configured secret variable. If the value exists, it returns the secret text. If it is missing or empty, it raises a RuntimeError, turning a bad deployment setup into an immediate visible failure.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this before signing or checking a token. That keeps the secret source centralized, so token creation and token verification always use the same deployment-level value.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### Harness signed tokens
Harness authentication code defines login, surface-routing, and shared compact token signing formats, then exposes login-token checking through the SDK.

### `core/src/ufo/harness/auth/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this folder with imports such as `ufo.harness.auth...` and expect Python to find modules inside it. Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it tells the system that this drawer exists and can be opened by name. Without this file, some Python setups or tools might not recognize the `auth` folder as part of the package structure, which could make authentication-related harness modules harder or impossible to import consistently. Because the file is empty, it does not run setup code, expose shortcuts, or change behavior when imported.


### `core/src/ufo/harness/auth/bearer.py`

`domain_logic` · `token minting and request authentication`

This file is the shared rulebook for UFO bearer tokens. A bearer token is like a signed ticket: whoever presents it can be treated as the member named inside, but only if the signature proves the ticket was made with the system’s secret key.

The token contains three pieces of information: the workspace id, the member email address, and an expiry time. The file turns that small JSON message into URL-safe text, signs it with HMAC-SHA256, and joins the two parts with a dot. HMAC is a way to make a tamper-proof signature using a shared secret. If anyone changes the workspace, email, or expiry, the signature no longer matches.

The important point is that there is no server-side session record to look up. Verification is self-contained: read the secret from the UFO_TOKEN_SECRET environment variable, check the signature, decode the payload, check the expiry, and then return the trusted claim. Without this file, token issuers and token checkers could drift into different formats, or worse, accept forged or expired credentials.

It also defines the fixed browser paths and cookie name used around login and logout, so the token’s web-facing behavior stays consistent.

#### Function details

##### `mint_token`  (lines 37–54)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed token for one workspace and one email address, valid for a limited time. It is used when the system needs to give a user a portable proof of membership.

**Data flow**: It receives a secret key, workspace id, email address, time-to-live, and optionally a current time. It trims and lowercases the email, calculates the expiry time, writes the claims as compact JSON, encodes that JSON in URL-safe base64 text, signs that text with HMAC-SHA256, and returns one string containing the encoded body plus its signature. If the secret is empty, it stops with an error instead of making an unsafe token.

**Call relations**: This is the creation side of the same format that verified_claims reads later. It relies on standard JSON, base64, time, and HMAC tools to produce a token that other surfaces can verify without asking a database.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 57–80)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a token is genuine and still valid, then returns the workspace and email it proves. If anything looks wrong, it returns nothing instead of trusting the token.

**Data flow**: It receives a token string and optionally a current timestamp. It reads the signing secret through _secret, splits the token into body and signature, recomputes the expected signature, compares signatures safely, decodes the body through _b64url_decode, parses the JSON, checks that the workspace, email, and expiry have the right shapes, and rejects expired tokens. On success it returns the workspace id and email; on failure it returns None.

**Call relations**: This is the main verification checkpoint. verify_token calls it when the process already knows which workspace the token must belong to, and workspace_claim calls it when the workspace needs to be discovered from the token itself. It hands decoding work to _b64url_decode and secret lookup to _secret.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 83–94)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Confirms that a token is valid for one specific workspace, then returns the authenticated member email. This prevents a token from one workspace being reused in another.

**Data flow**: It receives a token, the expected workspace UUID, and optionally a current timestamp. It asks verified_claims to prove the token first. If verification fails, or if the signed workspace does not match the expected workspace, it returns None. If everything matches, it returns the email in lowercase.

**Call relations**: This function sits one step above verified_claims. It is used when the caller already has a pinned workspace and needs both token authenticity and a workspace match before treating the request as authenticated.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 97–108)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace UUID from a valid token. This is useful when one running service can serve many workspaces and must learn which one a request belongs to.

**Data flow**: It receives a token and optionally a current timestamp. It first asks verified_claims to make sure the token is signed and unexpired. Then it tries to turn the signed workspace string into a UUID object. It returns that UUID on success, or None if the token is invalid or the workspace value is not a valid UUID.

**Call relations**: Like verify_token, this builds on verified_claims. Instead of comparing against a known workspace, it turns the trusted workspace claim into the form the rest of the system can use for routing or scoping the request.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 111–115)

```
def _secret() -> str
```

**Purpose**: Reads the shared token signing secret from the environment. It makes sure verification cannot quietly proceed without the key that protects the tokens.

**Data flow**: It looks for the UFO_TOKEN_SECRET environment variable. If it finds a non-empty value, it returns that string. If the value is missing or empty, it raises an error explaining that the secret is required.

**Call relations**: verified_claims calls this before checking a token signature. That keeps secret access in one small place, so callers can verify tokens without directly handling the secret.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 118–119)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the URL-safe base64 text used for the token body. It also restores any missing padding characters, because the token format strips them to keep the token shorter and cleaner.

**Data flow**: It receives the encoded body text. It adds the right number of equals signs needed by the base64 decoder, decodes the text back into bytes, and returns those bytes for JSON parsing.

**Call relations**: verified_claims calls this after the token signature has matched. It is a small helper that hides the padding detail from the main verification flow.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/harness/auth/surface_token.py`

`domain_logic` · `link generation and request handling`

A surface token is like a tamper-proof address label on a package. The label may say which workspace or target a route should use, but the route still decides whether the visitor is allowed in. This file only proves that the label was made by this deployment and was meant for this specific surface.

The core problem is that some routes are reached by link alone, before there is a cookie or logged-in session to say which workspace the request belongs to. To solve that, `mint_surface_token` takes a surface name and a set of string claims, adds the surface name into the signed data, turns it into compact JSON, and signs it using the deployment-wide secret from the environment. The result is an opaque token that can safely travel in a URL.

Later, `verify_surface_token` checks the token. It first verifies the signature using the same secret, then parses the JSON, then makes sure the embedded surface name matches the route that is asking. This prevents a token made for one surface from being reused at another. It also rejects malformed payloads and claims that are not strings, because URL-carried claims are expected to be plain text.

One important point: these tokens do not expire and do not grant permission by themselves. They are stable signed addresses, not access passes.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: Creates a signed token for one named surface, carrying only the claims that route will later need. It refuses unsafe input, such as an empty surface name or a caller trying to supply the reserved `surface` claim themselves.

**Data flow**: It receives a surface name and a mapping of string claims. It reads the shared token secret from the environment through `_secret`, adds the surface name to the claims, serializes the combined data as compact JSON, and passes those bytes to the shared token-signing helper. It returns a signed string token that can be placed in a URL.

**Call relations**: This is the issuing side of the flow. When something needs to build a durable surface link, it calls this function; this function relies on `_secret` to fetch the signing key and hands the prepared JSON body to `sign_token` so the body cannot be changed unnoticed.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: Checks whether a token is genuine, well-formed, and meant for the surface currently being accessed. If anything is wrong, it returns `None` instead of trusting the token.

**Data flow**: It receives the expected surface name and a token string. It reads the shared secret through `_secret`, asks the token verifier to confirm the signature and recover the original bytes, then parses those bytes as JSON. If the data is a dictionary, names the same surface, and contains only string keys and string values after removing the reserved surface claim, it returns those claims; otherwise it returns `None`.

**Call relations**: This is the receiving side of the flow, used when a request arrives with a surface token. It calls `_secret` to get the key needed for verification, delegates the cryptographic check to `verify_token`, then performs the surface-specific safety checks before giving route code any claims to use.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: Fetches the shared signing secret from the process environment. This keeps both token creation and token checking tied to the same deployment-wide secret.

**Data flow**: It reads the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the value is present, it returns that string; if it is missing or empty, it raises a runtime error because signing or verifying tokens would be unsafe or impossible without it.

**Call relations**: Both `mint_surface_token` and `verify_surface_token` call this helper before doing cryptographic work. It is the small gate that ensures the rest of the file never silently signs or verifies with a missing secret.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### `core/src/ufo/harness/auth/token_signing.py`

`util` · `cross-cutting`

This file is a small security helper. It turns raw payload bytes into an opaque-looking token made of two parts: the payload encoded in a web-safe text form, and a signature proving that the payload came from someone who knows the shared secret. The signature uses HMAC, which is like a tamper-evident seal made with a secret key: anyone with the key can check the seal, but someone without the key cannot make a valid new one.

The token format is simple: `body.signature`. The body is base64url text, which means binary bytes are rewritten as URL-friendly characters. The signature is also base64url text and is calculated from the body, not directly from the original bytes. When a token is checked, the file first makes sure it has the expected two-part shape, then recalculates the signature and compares it safely, then decodes the body back into the original bytes.

If anything is wrong, the code raises `SignedTokenError`: the token may be missing its separator, have the wrong signature, or contain unreadable payload text. Without this file, other parts of the harness would need to repeat delicate signing and verification code, increasing the chance of accepting forged or broken tokens.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: Creates a standalone signature for a message using a secret. This is useful when the data and its proof need to be kept as separate pieces.

**Data flow**: It receives a secret key as bytes and a message as bytes. It uses HMAC with SHA-256 to create a fixed-size digest, then rewrites that digest as URL-safe base64 text and removes extra padding characters. It returns the signature as a string.

**Call relations**: This is the basic sealing step used by the rest of the file. `sign_token` calls it to attach a signature to a token body, and `verify_detached` calls it to recreate the expected signature before comparing it with the one it was given.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: Checks whether a given signature really matches a message and secret. Someone would use it to decide whether a message has been tampered with.

**Data flow**: It receives the secret, the original message bytes, and a signature string. It recreates the correct signature by calling `sign_detached`, then compares the expected and supplied signatures using a safe comparison function designed for security-sensitive checks. It returns `True` if they match and `False` if they do not.

**Call relations**: This is the checking partner to `sign_detached`. `verify_token` calls it after splitting a token into body and signature, so token verification can focus on the larger token format while this function focuses only on the signature match.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Builds a complete signed token from raw payload bytes. This gives callers one compact string they can pass around while still being able to detect later changes.

**Data flow**: It receives a secret key and payload bytes. It encodes the payload as URL-safe base64 text, signs that text by calling `sign_detached`, then joins the encoded body and signature with a dot. It returns the finished token string.

**Call relations**: This function is the outward-facing creation step for tokens in this file. It depends on `sign_detached` for the cryptographic seal and packages that seal beside the encoded payload in the expected `body.signature` form.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks a complete signed token and returns the original payload if it is valid. It rejects tokens that are badly shaped, forged, or not decodable.

**Data flow**: It receives a token string and the secret key. It splits the token at the dot into body and signature, refuses the token if either part is missing, then calls `verify_detached` to confirm the signature. If the signature is valid, it decodes the base64url body back into bytes and returns those bytes. If any step fails, it raises `SignedTokenError` with a clear reason.

**Call relations**: This is the main reading and trust-checking step for tokens made by `sign_token`. It hands signature validation to `verify_detached`, then uses base64 decoding to recover the payload only after the token has passed the tamper check.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### `core/src/ufo/sdk/bearer.py`

`io_transport` · `request handling`

This file is a small bridge between outside-facing SDK code and the project’s internal bearer-token authentication code. A bearer token is like a temporary wristband: if someone presents it, the system can check whether it was issued by the trusted gateway and what workspace or session it belongs to. The important security idea here is that extensions can verify a token, but they never receive the signing secret itself. Instead, the verification functions look up the secret internally, through `UFO_TOKEN_SECRET`. Without this wrapper, extension authors might need to import deeper internal modules directly, or worse, be tempted to pass around secrets themselves. This file keeps the public surface simple and safer: it exposes the login path, logout path, session cookie name, and helper functions for verifying tokens and reading claims from them. The actual work still lives in `ufo.harness.auth.bearer`; this file is mostly a stable public doorway to that logic.


### Artifact download links
Stored artifacts are protected by secure, expiring, signature-checked download URLs.

### `core/src/ufo/runtime/media/artifact_url.py`

`domain_logic` · `artifact URL creation and download request handling`

This file is the gatekeeper for artifact download URLs. An artifact is a stored file, and the URL is like a temporary claim ticket: it names the file, says when the ticket expires, says which workspace owns the file, and carries a signature proving the ticket was made by the system. Without this file, anyone could more easily guess or tamper with artifact paths, old links might keep working forever, and browsers would have a harder time caching repeated downloads safely.

The file does three main jobs. First, it mints signed URLs. The stored blob key becomes the URL path, while the query string carries the expiry time, workspace id, signature, and sometimes an image-preview permission. Second, it verifies incoming URLs. It checks that ids and filenames are well formed, that the signature matches, that the workspace claim is present, and that the link has not expired. If a link is authentic but expired, it reports that separately so a signed-in workspace member can potentially refresh it. Third, it decides safe media types for files, especially avoiding host-dependent guesses for important document formats.

A key design detail is bucketed expiry. Instead of every minted URL being unique down to the second, expiry times are rounded to hourly boundaries. That means repeated requests for the same artifact can reuse the same URL and be cached, while still guaranteeing the link lasts at least the intended time.

#### Function details

##### `artifact_url_expiry`  (lines 59–66)

```
def artifact_url_expiry(now: datetime) -> int
```

**Purpose**: Chooses the expiry timestamp to put into a newly minted artifact URL. It rounds expiry up to a fixed time bucket so the same artifact gets the same URL during that bucket, which helps browser and edge caches reuse it.

**Data flow**: It receives the current time. It converts that time to seconds, adds the required lifetime, rounds up to the next bucket boundary, and returns that future timestamp as an integer.

**Call relations**: When an image preview link is being created, mint_image_preview_url asks this function for the expiry time. The returned value is then passed into mint_artifact_url so the signed URL and its signature agree on the same expiry.

*Call graph*: called by 1 (mint_image_preview_url); 1 external calls (timestamp).


##### `ArtifactUrlExpired.__init__`  (lines 88–90)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: Creates the special error used when a URL is genuine but too old to use directly. It preserves the verified claims so another part of the system can decide whether a logged-in workspace member may refresh the link.

**Data flow**: It receives already-verified artifact claims. It turns them into an error message saying the URL is expired, and stores the claims on the exception for later use.

**Call relations**: verify_artifact_url calls this after it has confirmed the signature but finds that the link has expired or lacks a workspace claim. This separates 'real but expired' from 'fake or malformed', which matters for refresh behavior.

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 93–105)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: Decides what internet media type, also called a MIME type, should be used when serving an artifact. This tells browsers whether a file is a patch, document, spreadsheet, image-like file, or just unknown bytes.

**Data flow**: It receives a filename. It first checks the project’s own fixed list for important suffixes, then asks Python’s mimetype database for other names, and returns a safe fallback if the type is unknown or the filename suggests compressed content.

**Call relations**: This function stands on its own as the file’s media-type lookup helper. It uses pathlib to inspect the filename suffix and mimetypes.guess_type for general guesses, but protects the product from relying only on host-specific MIME databases.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 108–131)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: Builds a signed relative download URL for one artifact blob in one workspace. Someone uses it when they want to give a browser a temporary link that can be checked later without requiring the link itself to reveal a password.

**Data flow**: It receives the signing secret, blob key, expiry time, workspace id, and optionally an image-preview grant. It checks that the secret exists, splits and validates the blob key, encodes any preview claim, signs the workspace, artifact id, expiry, and preview data, then returns a URL path with query parameters containing the grant.

**Call relations**: mint_image_preview_url calls this after deciding an image is eligible for preview. Inside, this function relies on _split_key to prove the blob key is in the artifact namespace, _parsed_preview to confirm any preview claim is valid, _signed_message to build the exact bytes to sign, sign_detached to make the signature, and quote to safely place the filename and preview value into a URL.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); called by 1 (mint_image_preview_url); 3 external calls (__init__, sign_detached, quote).


##### `mint_image_preview_url`  (lines 134–162)

```
def mint_image_preview_url(secret: str, public_base_url: str | None, blob_key: str, size_bytes: int | None, *, workspace_id: UUID) -> str | None
```

**Purpose**: Creates a full public URL for displaying a stored raster image inline, when the file is safe and eligible. Raster means a pixel-based image such as PNG or JPEG.

**Data flow**: It receives the signing secret, public base URL, blob key, byte size, and workspace id. If required settings are missing, the file is not a supported raster image, the size is missing, or the file is too large, it returns None. Otherwise it calculates an expiry, creates an image-preview grant, asks mint_artifact_url for a signed path, prefixes the public base URL, and returns the absolute URL.

**Call relations**: This is the high-level helper for preview links. It asks raster_image_media_type what kind of image the blob key names, asks artifact_url_expiry for a cache-friendly expiry time, constructs an ImagePreviewGrant, and hands the actual signing work to mint_artifact_url.

*Call graph*: calls 2 internal fn (artifact_url_expiry, mint_artifact_url); 3 external calls (__init__, now, raster_image_media_type).


##### `verify_artifact_url`  (lines 165–206)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether an incoming artifact URL is valid and returns the permissions it proves. It rejects malformed, tampered, wrongly scoped, or expired links so the download route can avoid serving unsafe bytes.

**Data flow**: It receives the secret, URL pieces such as artifact id, filename, expiry, signature, preview claim, workspace id, and the current time. It validates the id, filename, expiry, workspace id, and preview text; rebuilds the signed message; verifies the signature; constructs ArtifactClaims for the blob being requested; then either returns those claims or raises an error if the URL is expired or not directly servable.

**Call relations**: This is the counterpart to mint_artifact_url. It uses _is_canonical_uuid and _is_filename to reject suspicious path pieces, _parsed_preview to understand preview permissions, _signed_message to recreate exactly what should have been signed, and verify_detached to check the signature. If the link is authentic but expired or missing a workspace claim, it raises ArtifactUrlExpired with the claims instead of treating it like a forged URL.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 209–211)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: Builds the exact byte string that gets signed and later verified. This matters because signing only works if both sides agree on precisely the same message.

**Data flow**: It receives the workspace text, artifact id, expiry text, and preview text. It joins them in a fixed order, including the workspace prefix when present, encodes the result as bytes, and returns those bytes.

**Call relations**: mint_artifact_url uses this before creating a signature, and verify_artifact_url uses it before checking one. It is the shared recipe that keeps minting and verification in sync.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 214–225)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: Turns a preview claim from URL text into a structured image-preview grant, but only if the claim is safe and allowed. It prevents a URL from falsely claiming that arbitrary bytes should be rendered inline as an image.

**Data flow**: It receives a string such as a media type plus byte size. It splits out the media type and size, checks that the media type is one of the allowed raster image types, checks that the size is numeric and within the maximum preview size, and returns an ImagePreviewGrant. If any check fails, it returns None.

**Call relations**: mint_artifact_url uses this as a self-check before signing a preview claim. verify_artifact_url uses it when reading an incoming preview parameter, so only the same allowed preview format can pass verification.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 228–237)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: Checks and splits a stored artifact blob key into its artifact id and filename. It makes sure signed URLs can only point inside the intended artifact storage area.

**Data flow**: It receives a blob key string. It removes the required artifact prefix, separates the id from the filename, checks that the id is a canonical UUID and the filename is a simple single path segment, and returns the two pieces. If the key is not a valid artifact address, it raises an ArtifactUrlError.

**Call relations**: mint_artifact_url calls this before signing any link. It delegates the id check to _is_canonical_uuid and the filename check to _is_filename, so malformed or path-traversal-like blob keys never become downloadable URLs.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 240–244)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: Checks whether a string is a UUID in the project’s exact normal form. A UUID is a standard unique identifier, and the canonical check prevents alternate spellings from being treated as equivalent in signed data.

**Data flow**: It receives a string. It tries to parse it as a UUID, converts it back to the standard string form, and returns true only if that standard form exactly matches the original input. If parsing fails, it returns false.

**Call relations**: _split_key uses this when minting URLs from blob keys, and verify_artifact_url uses it when checking incoming artifact and workspace ids. This keeps both creation and verification strict about identifier format.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 247–248)

```
def _is_filename(value: str) -> bool
```

**Purpose**: Checks whether a filename is a safe single filename rather than a path. It prevents names like empty strings, '.', '..', or names containing slashes from being used to escape the artifact’s folder.

**Data flow**: It receives a filename string. It returns true only when the value is not empty, does not contain '/', and is not one of the special directory markers '.' or '..'.

**Call relations**: _split_key uses this before minting a URL, and verify_artifact_url uses it before accepting a requested filename. This keeps the artifact id as the only directory-like part of the URL and stops the filename from changing where the system looks for bytes.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### External sign-in completion
External account connection flows return through web endpoints and callback pages, including the Anthropic credential-linking implementation.

### `core/src/ufo/runtime/surfaces/cli.py`

`io_transport` · `request handling`

This file is the public landing spot for an OAuth callback. OAuth is the common “sign in or connect this account through another service” flow: the outside provider sends the browser back with a short code, and this endpoint turns that code into a finished connection. The browser is not carrying a normal logged-in session here. Instead, it carries a sealed state value, like a tamper-proof claim ticket, which says which member, agent, and conversation started the connection.

The main route, `connect_callback`, checks that the provider sent both the sealed state and the code. It asks the installed connection system to finish the flow. If the connection system is unavailable, the state is bad, or the provider is unknown, it returns a clear HTTP error. If everything works, it shows a simple HTML page saying the account is connected. If the original conversation was resumed, the page says the conversation continues; otherwise it tells the member they can close the page.

The second route, `connect_logo`, serves the SVG logo used by that page. This matters because the callback page may be reached without the normal frontend app loaded, so it needs a stable logo URL from the backend itself.

#### Function details

##### `connect_callback`  (lines 38–64)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: This is the browser return endpoint for completing an account connection after an external provider redirects back. It verifies that the return contains the needed proof, finishes the connection, and shows a final “connected” page.

**Data flow**: The browser sends in a `state` value and a `code` value. The function first asks for the installed connection flow; if that system is unavailable, it turns that into a service-unavailable web error. It then rejects missing inputs, asks the flow to complete the connection, translates bad state or unknown provider problems into web errors, and finally builds a short success page naming the connected provider and account. The output is an HTML response shown in the member’s browser.

**Call relations**: FastAPI runs this function when a request reaches the connect callback URL. The function relies on `installed_connect_flow` to find the connection machinery, uses web exceptions to stop the request with the right error when something is wrong, and hands the final success message to `callback_page` so all return pages look and behave the same.

*Call graph*: 3 external calls (HTTPException, installed_connect_flow, callback_page).


##### `connect_logo`  (lines 68–76)

```
async def connect_logo() -> Response
```

**Purpose**: This serves the UFO logo image used by the connection callback page. It gives the callback page a dependable logo even when the normal frontend application is not available.

**Data flow**: A browser asks for the logo URL. The function reads the SVG file stored next to this backend code, wraps those bytes in a web response, marks it as an SVG image, and adds a long-lived cache header so browsers can safely keep it. The output is the logo image response.

**Call relations**: FastAPI runs this function when the logo URL is requested, usually by the callback page in the browser. It hands the file contents to FastAPI’s `Response` object, which turns the bytes and headers into the actual HTTP response sent back to the browser.

*Call graph*: 1 external calls (Response).


### `core/src/ufo/sdk/callback_page.py`

`io_transport` · `request handling`

When someone starts a connection from Slack, a command line tool, or an install link, they may end up in a browser page after approving something with another provider. At that moment, the system may not have a normal signed-in web session for them. This file creates a simple, self-contained “you are done” page that only says what just happened and what the person should do next.

The page is deliberately tiny. It uses the browser’s built-in light or dark color theme, fetches only the product logo, and avoids extra fonts or styling. That matters because this page may be opened on a phone, from a chat link, and should load quickly.

The file defines a reusable page template, a small PageLink data object for “go back here” buttons, and the callback_page function that fills in the template. The function safely escapes all user-facing text before placing it into HTML, which helps prevent accidental or malicious HTML from being inserted into the page. It can add a link back to a conversation, add a short detail message, return a non-200 HTTP status if needed, and optionally include a script that asks the browser to close the tab after a short delay. Since browsers often refuse to close tabs they did not open by script, the link is the reliable fallback.

#### Function details

##### `callback_page`  (lines 70–90)

```
def callback_page(*, headline: str, detail: str='', link: PageLink | None=None, status: int=200, close: bool=False) -> HTMLResponse
```

**Purpose**: This function creates the final HTML response shown after a browser-based callback or install step. Callers use it to tell the person what happened, optionally show a return link, and optionally try to close the browser tab.

**Data flow**: It receives a headline, an optional detail line, an optional PageLink with button text and a destination URL, an HTTP status code, and a flag saying whether to try closing the tab. It escapes the visible text and URL so they are treated as plain content rather than executable page markup, inserts the safe values into the prepared HTML template, adds link styling only when a link exists, adds the close script only when requested, and returns an HTMLResponse with the finished page and status code.

**Call relations**: A callback or install route calls this function when it needs to answer the browser at the end of an external flow. Inside, it relies on html.escape to make the inserted text safe for a web page, then hands the completed HTML to ufo.sdk.http.HTMLResponse so the web layer can send it back to the browser.

*Call graph*: 2 external calls (escape, HTMLResponse).


### `extensions/web/ufo_ext_web/anthropic_login.py`

`domain_logic` · `credential sign-in and verification`

This file is the bridge between this web app and Anthropic sign-in. Its job is to make sure a member can prove they have a usable Anthropic credential before that credential is saved for their account. Without it, the app could not safely accept a pasted Anthropic code or API key, and users might store broken or fake credentials.

There are two main paths. If the deployment has an Anthropic OAuth client ID, `AnthropicCodeLogin` creates a special Anthropic authorization link. The user opens that link, signs in with Anthropic, and Anthropic shows them a code. Because Anthropic displays the code on its own page instead of redirecting back to this app, the user acts like the courier: they paste the code back into this system.

The file also protects the exchange with a verifier, which is like a matching ticket stub kept in the browser. When the pasted code comes back, the verifier must match before the app asks Anthropic to trade the code for an access token.

For either OAuth access tokens or plain API keys, `verified_key` performs a practical test: it asks Anthropic for the list of available models. If Anthropic answers successfully, the credential is considered real enough to store. If the network fails or Anthropic rejects it, the credential is refused.

#### Function details

##### `AnthropicCodeLogin.authorize`  (lines 73–96)

```
def authorize(self) -> PendingAuthorization
```

**Purpose**: Starts the Anthropic OAuth sign-in journey. It creates a secure verifier, builds the Anthropic authorization URL, and returns both the URL the user should visit and the browser cookie value needed to finish the flow later.

**Data flow**: It starts with the configured Anthropic client ID, authorization address, and redirect address. It creates a random verifier, turns that into a matching challenge, places the required OAuth details into a URL query string, and returns a `PendingAuthorization` containing the finished link plus the verifier to keep in the user's browser. Nothing is sent to Anthropic yet; this only prepares the trip.

**Call relations**: This is used at the beginning of the Anthropic code sign-in flow. It relies on standard library helpers to create safe random bytes, encode them for URLs, hash the verifier into a challenge, and build the final query string. The result is handed back to the web layer so the member can be sent to Anthropic and the verifier can be saved for the later claim step.

*Call graph*: 5 external calls (__init__, urlsafe_b64encode, sha256, token_bytes, urlencode).


##### `AnthropicCodeLogin.claim`  (lines 98–132)

```
async def claim(self, pasted: str, verifier: str) -> Grant | None
```

**Purpose**: Finishes the Anthropic OAuth sign-in journey after the user pastes back the code. It checks that the pasted information matches the browser's saved verifier, then asks Anthropic to exchange the code for an access token.

**Data flow**: It receives the user's pasted text and the verifier saved from the earlier authorization step. It first extracts the code and optional state from the pasted text, rejects missing or mismatched values, then sends a token request to Anthropic. If Anthropic responds successfully and the response can be turned into a `Grant`, it returns that grant; otherwise it returns `None` and nothing is accepted.

**Call relations**: This function runs after `AnthropicCodeLogin.authorize` has created the sign-in link and cookie verifier. It calls `_split_pasted` because Anthropic may give the user a bare code, a `code#state` pair, or a full URL. It then uses an HTTP client to contact Anthropic's token endpoint and passes the successful JSON response to `ufo.sdk.models.granted`, which turns Anthropic's answer into the system's grant object.

*Call graph*: calls 1 internal fn (_split_pasted); 4 external calls (AsyncClient, dumps, compare_digest, granted).


##### `_split_pasted`  (lines 135–147)

```
def _split_pasted(pasted: str) -> tuple[str, str | None]
```

**Purpose**: Understands the different ways Anthropic might show the user their authorization code. It accepts a full callback URL, a `code#state` pair, or just a plain code, and pulls out the parts the app needs.

**Data flow**: It receives the raw text the user pasted. It trims extra spaces, then checks its shape: if it looks like a URL, it reads `code` and `state` from the URL query or fragment; if it contains `#`, it treats the text before `#` as the code and the text after it as the state; otherwise it treats the whole text as the code. It returns the code plus either the state or `None`.

**Call relations**: This is a small helper used by `AnthropicCodeLogin.claim` before any token exchange happens. Its role is to make the user-facing paste step forgiving, so the rest of the claim logic can work with one simple pair of values instead of several possible input formats.

*Call graph*: called by 1 (claim); 2 external calls (parse_qs, urlsplit).


##### `verified_key`  (lines 150–164)

```
async def verified_key(credential: str) -> bool
```

**Purpose**: Checks whether an Anthropic credential really works before the system stores it. It does this by making a simple authenticated request to Anthropic and accepting the credential only if Anthropic replies successfully.

**Data flow**: It receives a credential string. If the string looks like an Anthropic OAuth access token, it sends it as a bearer token with Anthropic's OAuth beta header; otherwise it sends it as an API key. It asks Anthropic's models endpoint for a response. If the request succeeds with HTTP status 200, it returns `true`; if Anthropic rejects it or the network call fails, it returns `false`.

**Call relations**: This function is used after a user provides or obtains a credential, before that value is saved in the member's credential slot. It talks directly to Anthropic over HTTP and does not call the OAuth claim flow itself; instead, it acts as the final gatekeeper for both OAuth tokens and manually created API keys.

*Call graph*: 1 external calls (AsyncClient).

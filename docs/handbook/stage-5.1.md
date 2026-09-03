# Authentication, login, OAuth, and signed ingress tokens  `stage-5.1`

This stage is the system’s front door and badge-checking desk. It is shared support used when people sign in, connect outside accounts, or open browser routes that need a trusted identity. The core auth package starts with a simple package marker so other code can import it. Its token files create signed tokens: small pieces of data protected with a secret so the system can later tell they were made by UFO and were not changed. Bearer tokens identify a member and workspace. Surface tokens label public routes with the right workspace before cookies exist. Sandbox ingress tokens add expiry and keep different browser access uses from being mixed up.

The extension files handle real account-connection journeys. The web extension connects personal Anthropic or OpenAI credentials and checks them before saving. The CLI surface finishes OAuth redirects and shows the completion logo. Composio and Pipedream act as bridges to hosted consent pages. Slack tools help install and search Slack. The iMessage tool lets an approved member reserve and verify a phone number. Together, these pieces let UFO know who is arriving and safely link the outside services they want to use.

## Files in this stage

### Signed token foundations
Core authentication helpers define the package and issue or verify tamper-proof member, surface, and sandbox ingress tokens.

### `core/src/ufo/harness/auth/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that folder is `ufo.harness.auth`, which likely groups code related to authentication for the harness part of the system.

There are no functions, classes, settings, or side effects in this file. Its value is structural: it gives the project a clear place for authentication code and allows imports such as `ufo.harness.auth.some_module` to work consistently. Think of it like a label on a drawer. The label does not do the work, but it makes the drawer part of the organized cabinet and lets the rest of the system find what belongs there.

If this file were removed, behavior would depend on the Python version and packaging setup. Modern Python can sometimes use folders without `__init__.py` as “namespace packages,” but keeping this file makes the package boundary explicit and avoids surprises in tools, tests, or environments that expect traditional packages.


### `core/src/ufo/harness/auth/bearer.py`

`domain_logic` · `token minting and request authentication`

This file is the shared rulebook for UFO bearer tokens. A bearer token is like a signed wristband: whoever presents it can be treated as the named member, but only if the signature proves UFO issued it and the wristband has not expired.

The token contains three plain claims: the workspace id, the member email address, and an expiry time. Those claims are turned into compact JSON, encoded with URL-safe base64, then signed with HMAC-SHA256. HMAC is a way to make a tamper-evident signature using a shared secret key. If anyone changes even one character in the token body, the signature check fails.

The important secret comes from the `UFO_TOKEN_SECRET` environment variable. Code that verifies tokens reads the secret here, so callers can pass in a token and receive trusted claims without handling the key themselves.

The file supports two main verification styles. `verify_token` checks that a token belongs to one specific workspace, useful when a running process is pinned to one tenant. `workspace_claim` instead extracts the signed workspace id from the token, useful for a shared service that serves many workspaces. The constants for login, logout, join, and the session cookie name keep the browser-facing authentication paths tied to the same token format.

#### Function details

##### `mint_token`  (lines 37–54)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: This function creates a signed bearer token for one email address in one workspace. It is used when UFO needs to issue a fresh login token that later verifiers can trust.

**Data flow**: It receives a secret key, a workspace id, an email address, a time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates the expiry time, serializes the claims as compact JSON, base64-encodes that body, signs the body with HMAC-SHA256, and returns one string shaped like `body.signature`. If the secret is empty, it stops with an error instead of creating an unsafe token.

**Call relations**: This is the issuing side of the token story. It uses standard library tools for JSON, base64 encoding, time, and HMAC signing. The tokens it creates are meant to be read later by `verified_claims`, which checks the same signed shape.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 57–80)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: This function checks whether a bearer token is genuine and still valid. If it passes, it returns the workspace id and email claim; if anything looks wrong, it returns nothing.

**Data flow**: It receives a token string and optionally a current timestamp for testing or controlled checks. It reads the signing secret from the environment, splits the token into body and signature, recomputes the expected signature, compares signatures safely, decodes the base64 body, parses the JSON, checks that the expected fields have the right types, and rejects expired tokens. The output is either `(workspace_id, email)` or `None`.

**Call relations**: `verify_token` and `workspace_claim` call this first because neither of them should trust token contents before the signature and expiry have been checked. Inside, it relies on `_secret` to fetch the shared key and `_b64url_decode` to turn the compact token body back into JSON bytes.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 83–94)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: This function authenticates a token for one known workspace. It answers the practical question: “Does this token prove a member belongs to this exact workspace, and if so, what is their email?”

**Data flow**: It receives a token, a workspace UUID, and optionally a current timestamp. It asks `verified_claims` to prove the token is signed and unexpired, then compares the token’s workspace claim with the workspace it was given. If they match, it returns the member email in lowercase; otherwise it returns `None`.

**Call relations**: This function sits one step above general token verification. After `verified_claims` establishes that the token itself is trustworthy, `verify_token` adds the tenant boundary check so a token from one workspace cannot be used in another.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 97–108)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: This function extracts the workspace named by a valid token. It is useful when one running service handles many workspaces and must decide the workspace from each request.

**Data flow**: It receives a token and optionally a current timestamp. It first asks `verified_claims` to check the signature and expiry. If that succeeds, it tries to turn the workspace claim into a real UUID object. It returns that UUID when valid, or `None` when verification fails or the workspace text is not a valid UUID.

**Call relations**: Like `verify_token`, this builds on `verified_claims`. Instead of comparing against a preselected workspace, it hands the verified workspace id outward so the shared fleet can route or scope the request correctly.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 111–115)

```
def _secret() -> str
```

**Purpose**: This helper fetches the token signing secret from the environment. It makes sure verification never silently proceeds without the key needed to prove tokens are genuine.

**Data flow**: It reads the `UFO_TOKEN_SECRET` environment variable. If a value is present, it returns that value. If it is missing or empty, it raises an error explaining that the secret must be set.

**Call relations**: `verified_claims` calls this before checking any token signature. That keeps the secret lookup in one place and ensures all verification uses the same configured key.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 118–119)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: This helper decodes the compact base64 text used inside tokens. It hides the small detail that token bodies omit normal base64 padding characters.

**Data flow**: It receives a base64-url-safe string without guaranteed padding. It adds the missing `=` padding characters if needed, decodes the text into bytes, and returns those bytes for JSON parsing.

**Call relations**: `verified_claims` calls this after the token signature has already matched. Its job is only to make the signed token body readable again so the claims can be inspected.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/harness/auth/surface_token.py`

`domain_logic` · `request handling`

Some routes can be reached just by opening a link. At that point, the system may not have a cookie or logged-in session to say which workspace the request belongs to. This file solves that by putting the needed routing claims into a signed token that can travel in a URL.

The important idea is that the token is trusted only if its signature matches a shared secret from the environment. A signature here is like a wax seal on an envelope: anyone can carry the envelope, but if they change what is inside, the seal no longer matches. The file uses the deploy-wide UFO token secret, the same secret source used by bearer-token authentication elsewhere.

A surface token always includes the name of the surface that created it. When the token is checked, the caller must name the expected surface, and the token is rejected if it was minted for a different one. This prevents a token from one public route being reused as an address for another.

The payload claims are deliberately simple: every key and value must be a string, because the data is meant to cross a URL. The token does not expire and does not grant access on its own. It only helps the route find the right context; the route still has to apply its normal access checks.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: Creates a signed token for one specific surface. It is used when the system needs to place trusted routing information into a URL without exposing the signing secret.

**Data flow**: It receives a surface name and a mapping of string claims. It first rejects an empty surface name and rejects any claim that tries to use the reserved key "surface". Then it builds a small JSON body containing the surface name plus the supplied claims, reads the signing secret through _secret, and signs the JSON bytes. The result is an opaque token string that can be put into a link.

**Call relations**: When a surface needs to hand out a stable link, this function packages the route-identifying claims. It asks _secret for the deploy-wide secret, turns the claims into JSON with json.dumps, and hands the bytes to sign_token so the token can later be checked for tampering.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: Checks whether a token is a valid address token for a particular surface, and returns its claims if it is. It returns None instead of trusting anything that is forged, malformed, meant for another surface, or not made only of string claims.

**Data flow**: It receives the expected surface name and a token string. It reads the secret through _secret and uses it to verify the token signature; if that fails, it returns None. If the signature is valid, it parses the JSON payload, checks that it is a dictionary, checks that its reserved "surface" value matches the expected surface, removes that reserved field, and confirms all remaining keys and values are strings. On success it returns the remaining claims as a dictionary.

**Call relations**: A route uses this when a request arrives with a surface token in its URL. The function relies on verify_token to prove the token was signed with this deploy’s secret, uses json.loads to recover the stored claims, and then performs the surface-name and string-shape checks before giving the route anything it can resolve by.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: Fetches the shared signing secret from the process environment. It centralizes the rule that surface tokens cannot be created or checked unless the required secret is configured.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the variable is present and non-empty, it returns that string. If it is missing or empty, it raises a RuntimeError, stopping token minting or verification because there is no safe secret to use.

**Call relations**: Both mint_surface_token and verify_surface_token call this before signing or verifying a token. It is the small gatekeeper that connects this file’s token logic to the deploy’s configured secret, ensuring both creation and checking use the same source of truth.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### `core/src/ufo/harness/auth/token_signing.py`

`domain_logic` · `request handling`

This file solves a common authentication problem: how to give another part of the system a short token that can be read back later, while making sure nobody edited it. It does this with HMAC, which is a cryptographic signature made from a secret key and a message. Think of it like sealing an envelope with a wax stamp that only trusted code can make.

The token has two parts separated by a dot. The first part is the payload, encoded with base64url, which turns raw bytes into text that is safe to put in URLs or headers. The second part is a signature of that encoded payload. The payload is not encrypted, so this is not meant to hide secret contents. Its job is integrity: proving the contents are exactly what the signer produced.

When making a token, the file encodes the payload and signs that encoded text. When checking a token, it splits the token, verifies the signature, and only then decodes the payload back into bytes. Bad shape, bad signature, or unreadable payload all become a clear SignedTokenError. This gives the rest of the authentication code a simple rule: either get trusted bytes back, or reject the token.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: This function makes a standalone signature for a message using a secret key. It is used when the code needs proof that a specific byte string came from someone who knows the secret.

**Data flow**: It receives a secret key and a message, both as bytes. It combines them with HMAC-SHA256, a standard way to make a secure keyed fingerprint, then turns the raw fingerprint into URL-safe text without padding characters. The result is a string signature that can be stored or sent next to the message.

**Call relations**: This is the low-level stamp maker for the file. sign_token calls it when building a full token, and verify_detached calls it to recreate the expected stamp before comparing it with one it was given.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: This function checks whether a given signature really matches a message and secret. It answers yes or no without decoding or interpreting the message itself.

**Data flow**: It receives a secret key, the original message bytes, and a signature string to check. It recomputes the correct signature with sign_detached, then compares the two signatures using a special constant-time comparison, which avoids leaking clues through timing differences. It returns true if they match and false if they do not.

**Call relations**: This is the signature checker used by verify_token. In the larger flow, verify_token first pulls the token apart, then asks verify_detached whether the signature proves the token body is trustworthy.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: This function turns raw payload bytes into a complete signed token string. Code would use it when it wants to hand out data that can later be checked for tampering.

**Data flow**: It receives a secret key and payload bytes. First it encodes the payload as URL-safe base64 text and removes extra padding characters to keep the token compact. Then it signs that encoded body with sign_detached. It returns one string made from the body, a dot, and the signature.

**Call relations**: This is the public token maker in the file. It relies on sign_detached for the cryptographic stamp, and produces the format that verify_token expects to receive later.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: This function checks a signed token and returns the original payload bytes if the token is valid. If the token is malformed, changed, or unreadable, it raises SignedTokenError so callers can reject it cleanly.

**Data flow**: It receives a token string and a secret key. It splits the token into the encoded body and signature, rejects it if the dot separator or signature is missing, and asks verify_detached whether the signature matches. If the signature is valid, it restores any needed base64 padding and decodes the body back into bytes. The output is the trusted payload bytes; the failure path is a SignedTokenError with a specific reason.

**Call relations**: This is the public token reader in the file. It is the counterpart to sign_token: sign_token creates the body-plus-signature format, and verify_token enforces that format before handing the payload back to the authentication flow.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### `core/src/ufo/harness/sandbox/ingress_token.py`

`domain_logic` · `request handling and startup validation`

This file is a small security gate for sandbox ingress, meaning access from the outside world into a sandboxed app port. A token here is like a temporary, tamper-proof visitor badge. It says which workspace and conversation the visitor belongs to, which port they may reach, and when the badge expires. The badge is signed with a deploy-wide secret, so if someone edits the workspace, port, expiry, or token type, verification fails.

The file defines the shapes of the information carried inside a token. `IngressClaims` is the main claim set. It can also include a `shipped` bundle reference, for serving a prebuilt app bundle, and a `framer` reference, for a related enclosing site frame.

There are three token kinds. A “view” token is used when opening the ingress view path. A “session” token is later used as the browser’s cookie for that origin. A “report” token is for reporting an unreachable site, not for opening a visit. The token kind is included in the signed body, so each place can demand exactly the kind it expects.

Verification is deliberately strict. The token must have a valid signature, valid JSON, the expected kind, usable UUIDs, a real TCP port number, and an expiry time still in the future. Without this file, sandbox ports could not be safely exposed through shareable links and browser cookies.

#### Function details

##### `mint_ingress_token`  (lines 81–98)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns trusted ingress claims into a signed token string. It is used when the system wants to give a browser or another service a time-limited permission badge for one exact token kind.

**Data flow**: It receives an `IngressClaims` object and a token kind. It copies the workspace, conversation, port, expiry, and any optional `shipped` or `framer` details into a JSON-friendly body, reads the shared ingress secret, and signs that body. The result is a string that can later be checked for tampering and expiry.

**Call relations**: When some other part of the ingress flow needs to issue a view, session, or report token, it calls this function. This function asks `ingress_secret` for the deploy secret, turns the claims into JSON, and hands the bytes to the shared token-signing helper so the matching verifier can trust them later.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 101–140)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks a token and returns the permissions it grants, but only if the token is live, correctly signed, well formed, and exactly the expected kind. Callers use it before allowing access to a sandbox port or accepting a site report.

**Data flow**: It receives a token string, the current time, and the token kind the caller expects. It reads the shared secret, verifies the signature, parses the JSON, checks that the kind matches, rebuilds the claim objects, rejects invalid port numbers, and rejects expired tokens. If everything is valid, it returns an `IngressClaims` object; otherwise it raises `IngressTokenError` with a simple invalid-or-expired reason.

**Call relations**: This is the counterpart to `mint_ingress_token`. A request path, cookie check, or report endpoint calls it when a token arrives. It uses `ingress_secret` to get the same deploy secret used at minting time, delegates the signature check to the shared token verifier, and then performs ingress-specific checks such as token kind and port range.

*Call graph*: calls 1 internal fn (ingress_secret); 8 external calls (__init__, __init__, __init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 143–150)

```
def ingress_secret() -> str
```

**Purpose**: This function reads the deploy-wide secret used to sign and verify ingress tokens. It fails loudly if the secret is missing, because running without it would make token security impossible.

**Data flow**: It reads the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the variable contains a value, that value is returned. If it is missing or empty, the function raises a runtime error, turning a bad deployment setup into an immediate visible failure.

**Call relations**: Both token creation and token verification call this function so they use the same secret source. The file’s comments note that the ingress process may call it during boot as a readiness check, preventing a misconfigured service from appearing healthy and then failing every viewer request.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### OAuth redirect bridges
Browser-facing endpoints and provider bridges complete hosted OAuth-style account connection flows and return results to UFO.

### `core/src/ufo/runtime/surfaces/cli.py`

`io_transport` · `request handling`

This file is the landing place for an OAuth callback. OAuth is the common “sign in or connect an account on another site” flow where a provider sends the browser back with a temporary code. In this project, that return page cannot depend on a logged-in web session or a full frontend app, so core serves the final page itself.

The main route, `connect_callback`, receives the provider’s redirect. It expects two pieces in the URL: `state`, a sealed value that proves which member, agent, and conversation started the connection, and `code`, the temporary provider code. It asks the installed connect flow to verify the state, trade the code for the connected account, and record the grant. If anything is missing or invalid, it returns a clear HTTP error instead of silently accepting a bad callback.

When the connection succeeds, it builds a simple human-readable account name and returns a small HTML page saying the account is connected. If the original conversation was resumed, the page says the conversation continues; otherwise it simply tells the member they can close the page.

The second route, `connect_logo`, serves a fixed SVG logo used by that completion page. It is served from here because this callback may be reached without the normal frontend assets being available.

#### Function details

##### `connect_callback`  (lines 38–64)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: This is the browser return point after a user approves an outside account connection. It checks that the callback is legitimate, completes the account connection, and returns a simple page telling the user what happened.

**Data flow**: A browser request comes in with `state` and `code` query values. The function first gets the installed connection flow; if that system is unavailable, it returns a service error. It rejects requests missing either value, then gives both values to the flow so it can verify the sealed state and exchange the code. The result is a recorded connected account, which is turned into a friendly display name and then into an HTML success page. Invalid state becomes a bad-request error, and an unknown provider becomes a not-found error.

**Call relations**: FastAPI calls this function when a request reaches `/v1/connect/callback`. The function delegates the real connection work to `installed_connect_flow()` and the returned flow’s completion step, because that code knows how to verify the state and talk to the provider. At the end it hands the success message to `callback_page`, which builds the small HTML page shown in the browser.

*Call graph*: 3 external calls (HTTPException, installed_connect_flow, callback_page).


##### `connect_logo`  (lines 68–76)

```
async def connect_logo() -> Response
```

**Purpose**: This serves the UFO logo image used on the callback completion page. It exists so the page can show the correct branding even when no normal frontend bundle or logged-in session is available.

**Data flow**: A browser asks for the logo SVG. The function reads the SVG file from the local assets directory, wraps those bytes in an HTTP response, labels it as `image/svg+xml`, and adds a long-lived cache header so browsers can safely keep using the same immutable image.

**Call relations**: FastAPI calls this function when a request reaches `/v1/connect/logo.svg`. It does not take part in the account-connection decision; it supports the page produced by `connect_callback` and related callback pages by supplying the static image they display.

*Call graph*: 1 external calls (Response).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `OAuth connect request handling`

ufo expects an OAuth provider to give it a normal authorization web address right away, but Composio needs an asynchronous API call to create that address. This file solves that mismatch. Instead of sending the browser directly to Composio at first, `ComposioOAuthProvider.authorize_url` sends it to this extension’s own `/ext/composio/oauth` route.

That route, `oauth_route`, has two jobs. On the first visit, it asks Composio to create a consent link for the requested provider and workspace, then redirects the browser there. This is like a receptionist who cannot issue the visitor badge directly, so they call the badge office first and then point the visitor to the right desk. After the user approves or fails the consent step, Composio sends the browser back to the same route. If Composio provides a connected account id, the route forwards the browser to ufo’s main callback and passes that id as the OAuth “code”. If consent failed, it returns a clear error instead of silently starting over.

Finally, `ComposioOAuthProvider.exchange` receives that account id and verifies with Composio that it belongs to the expected workspace user and provider before creating an `OAuthAccount`. The real tokens stay inside Composio; ufo stores only the connected account identity.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first web address that ufo should send the user to when they start connecting a Composio-backed account. It does not contact Composio yet; it points the browser to this extension’s own bridge route so the asynchronous Composio setup can happen there.

**Data flow**: It receives the sealed `state` value from ufo and the final `redirect_uri` that ufo wants to use later. It packages the provider name, state, and callback into query parameters, extracts the scheme and host from the callback address, and returns a URL under `/ext/composio/oauth`. Nothing is stored or changed.

**Call relations**: This is the first step in the connect story. ufo’s connect registry calls it when it needs an authorization URL. It relies on `_origin` to keep the bridge on the same web origin as the callback, and uses URL encoding so the state and callback survive the browser trip safely.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This turns the account id returned by Composio into the `OAuthAccount` object that ufo can bind to a workspace. It also checks with Composio that the account really belongs to this workspace’s Composio user and matches this provider, so a random account id cannot be slipped in.

**Data flow**: It receives the `code`, which in this flow is actually Composio’s connected account id, plus the workspace id. It builds the expected Composio user id for that workspace, asks the Composio client for the connected account, then tries to fetch a human-friendly label for it. It returns an `OAuthAccount` containing the verified account id and, if available, the label; it does not receive or store any OAuth secret token.

**Call relations**: This runs after `oauth_route` has forwarded the browser back to ufo’s core callback with the connected account id as the code. It asks `ufo_ext_composio.client.composio_client` for the Composio API client, then hands the verified result back to ufo’s account-binding flow.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge for both halves of the Composio consent journey. It starts the consent process by creating a Composio connect link, and later receives Composio’s return visit and forwards the result to ufo’s normal callback.

**Data flow**: It reads query parameters from the incoming request: the preserved `state`, the core `callback`, and sometimes Composio’s `connected_account_id` or `status`. If state or callback is missing, it returns a bad-request response. If an account id is present, it redirects to the callback with that id as the OAuth code. If Composio reports a status without an account id, it returns a clear failure message. Otherwise, it reads the provider name, builds a return URL back to itself, asks Composio for a consent link tied to the current workspace, and redirects the browser to that link.

**Call relations**: The URL produced by `ComposioOAuthProvider.authorize_url` sends the browser here first. On the start leg, this function calls `_origin` to build a safe return URL and uses the Composio client to mint the hosted consent link. On the return leg, it does not call Composio again; it simply passes the connected account id onward so ufo can call `ComposioOAuthProvider.exchange` next.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts just the base origin from a full URL, meaning the scheme and host such as `https://example.com`. It also rejects callback URLs that are not normal HTTP or HTTPS web addresses.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a host name. If the URL is valid, it returns only `scheme://host`. If not, it raises an error explaining that the OAuth bridge needs a callback with a scheme and host.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` use this helper when they need to place the bridge route on the same origin as a known callback URL. This keeps the redirect path predictable and avoids building OAuth URLs from incomplete or unsafe callback addresses.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Workspace connector setup
Connector tools guide signed-in workspace members through enabling iMessage, linking Pipedream accounts, and configuring Slack access.

### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file solves a real safety problem: a shared iMessage line should not start texting a person’s phone until that person proves they control it. The tool here creates that proof step. A member gives a phone number, and the code checks that the number is a valid US phone number, that the workspace has an iMessage provider connected, and that the number is not already owned by someone else.

If the workspace has not yet connected its iMessage provider, only an admin can bind it. After that, the tool reserves the requested phone number for the current member for a short time. Think of this like putting a hold on a library book while the member completes checkout.

If the number is already fully linked, the tool returns a connected result and tells the member which assigned line to text. If the number still needs proof, the tool asks the provider for an assigned sending line, creates a short opt-in code, stores that pending claim, and shares a QR code. The QR code and link open Messages with the required text already filled in, so the member does not have to copy a code by hand.

Most results are returned as small JSON messages marked as untrusted, meaning callers should treat them as user-facing instructions rather than trusted internal commands.

#### Function details

##### `opt_in_link`  (lines 37–41)

```
def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str
```

**Purpose**: Builds a phone-friendly link that opens Apple Messages with the opt-in text already filled in. This makes it easier for the member to prove they control the phone number without typing the code manually.

**Data flow**: It receives the assigned iMessage phone number and the opt-in code. It combines the standard opt-in text with the code, safely encodes that text for use inside a link, and returns an sms: link pointing at the assigned phone number.

**Call relations**: When the tool is preparing a pending opt-in response, _opt_in_result calls this function so the final instructions can include a tappable message link.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `opt_in_qr`  (lines 44–52)

```
def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes
```

**Purpose**: Creates a QR code image for the opt-in message. This is useful when the member is reading instructions on a desktop and needs an easy way to open the prefilled message on their phone.

**Data flow**: It receives the assigned phone number and opt-in code. It builds an SMS-style QR payload, asks the QR code library to render it as a PNG image in memory, and returns the image bytes.

**Call relations**: ImessageConnect.run calls this after it has a pending claim. The generated image is then shared back to the user as an artifact, so they can scan it with their phone camera.

*Call graph*: called by 1 (run); 2 external calls (BytesIO, make).


##### `_display_phone`  (lines 58–62)

```
def _display_phone(phone_number: str) -> str
```

**Purpose**: Formats US phone numbers in a friendlier way for human instructions. It turns a number like +14155550123 into something easier to read, such as (415) 555-0123.

**Data flow**: It receives a phone number string. If it matches the expected US +1 format, it rearranges the digits into a familiar display format; otherwise, it leaves the original text unchanged.

**Call relations**: ImessageConnect.run and _opt_in_result use this when building user-facing instructions, so messages are easier to understand without changing the actual stored phone number.

*Call graph*: called by 2 (run, _opt_in_result).


##### `ImessageConnectInput._e164`  (lines 73–85)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Checks and normalizes the phone number the member typed. It accepts common US phone number formatting, rejects letters or invalid numbers, and returns a standard +1 number.

**Data flow**: It receives the raw phone number text from the tool input. It trims spaces, removes punctuation such as parentheses or dashes, checks that the result is a valid 10-digit US number, and returns it in E.164 form, which means a standard international format like +14155550123.

**Call relations**: This validator runs as part of creating an ImessageConnectInput object, before ImessageConnect.run uses the number. That means the main connection flow can rely on getting a clean, predictable phone number.


##### `_result`  (lines 88–94)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Builds the standard response object returned by this tool. It packages a state, an instruction, and optional extra details into JSON text for the caller.

**Data flow**: It receives a short state such as connected or pending, a human-readable instruction, and any extra fields. It turns those into a JSON string, wraps that string in text content, and returns a ToolResult marked as untrusted.

**Call relations**: ImessageConnect.run uses this for success, failure, and permission messages. _opt_in_result also uses it so opt-in responses have the same shape as the rest of the tool’s replies.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 97–106)

```
def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult
```

**Purpose**: Creates the response for the case where the phone number is reserved but still needs proof. It tells the member exactly what text to send, where to send it, and includes a prefilled link.

**Data flow**: It receives the assigned phone number and opt-in code. It builds the required opt-in message, formats the phone number for display, creates the prefilled SMS link, and returns a pending ToolResult with those details.

**Call relations**: ImessageConnect.run calls this after creating or reusing a pending claim and sharing the QR code. This function gathers the helper pieces, including _display_phone, opt_in_link, and _result, into one user-facing response.

*Call graph*: calls 3 internal fn (_display_phone, _result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 113–168)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full iMessage phone connection process for one member. It checks who is asking, makes sure the workspace is connected to an iMessage provider, reserves or links the phone number, and tells the member what to do next.

**Data flow**: It receives the tool context, which contains information such as the current member, workspace extension services, permissions, storage, and artifact sharing, plus the validated phone number input. It checks that a signed-in member is present, gets the configured iMessage provider, binds the provider if an admin needs to do that, reserves the phone number for the member, and then either reports that the number is already connected or creates a pending opt-in claim with an assigned line and secret code. If an opt-in is needed, it stores the claim, shares a QR code image, and returns instructions for sending the proof text.

**Call relations**: This is the main entry used when the iMessage connect tool is invoked. It calls the smaller helpers to format responses, display phone numbers, create QR codes, and build opt-in instructions, while it hands provider-specific work to the MessageProvider and workspace state work to the context’s installation and storage services.

*Call graph*: calls 6 internal fn (share_artifact, speaker_is_admin, _display_phone, _opt_in_result, _result, opt_in_qr); 6 external calls (__init__, now, choice, claim_key, read_claim, uuid4).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`orchestration` · `OAuth connect flow`

ufo expects an OAuth provider to give it a normal authorization web address right away, but Pipedream requires an asynchronous API call first to create a temporary Connect token. This file solves that mismatch by sending the browser to a local extension route first. Think of it like a reception desk: instead of sending the visitor straight to the meeting room, it checks the appointment, prints the pass, and then points them to the right door.

The main class, `PipedreamOAuthProvider`, describes one connector provider. Its `authorize_url` does not go directly to Pipedream. It builds a URL back to this extension’s `/ext/pipedream/oauth` route, carrying the provider name, ufo’s sealed state value, and the callback URL that core ufo expects later.

The `oauth_route` function is the browser bridge. On the first visit, it creates a Pipedream Connect token for a state-specific external user, pins both the success and failure return URLs back to itself, and redirects the user to Pipedream’s hosted consent page. When Pipedream sends the browser back after success, the route finds the newest account for that same external user and app, then redirects to ufo core with the account id as the code.

Finally, `exchange` receives that account id, asks Pipedream for the connected account, double-checks it belongs to the expected app and state-owned user, optionally fetches a display label, and returns an `OAuthAccount`. This prevents overlapping browser callbacks from accidentally binding the wrong account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 53–55)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This starts the account-connection journey by giving ufo a URL to open in the user’s browser. Instead of pointing directly at Pipedream, it points at this extension’s own bridge route so the extension can create the needed Pipedream token first.

**Data flow**: It receives ufo’s sealed `state` value and the final `redirect_uri` callback. It packages the provider name, state, and callback into query parameters, takes the scheme and host from the callback URL, and returns a full bridge URL under `/ext/pipedream/oauth`.

**Call relations**: ufo core calls this when it needs an authorization URL for a connector. It uses `_origin` to keep the bridge on the same web origin as the callback, then hands the browser off to `oauth_route`, which performs the asynchronous Pipedream setup.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 57–71)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This finishes the connection by turning the returned Pipedream account id into ufo’s stored OAuth account record. It also verifies that the account really belongs to the expected Pipedream app before ufo binds it to the grant.

**Data flow**: It receives the `code` value, which in this flow is a Pipedream account id, plus the workspace id and sealed state. It derives the state-specific Pipedream external user id, asks Pipedream for that connected account, checks that the app matches this provider, tries to fetch a human-friendly account label, and returns an `OAuthAccount` containing the account id and optional label. If the account is for the wrong app, it raises a Pipedream error instead of accepting it.

**Call relations**: ufo core calls this after the bridge route redirects back with a code. It calls into the Pipedream client to retrieve and verify the exact account, then hands core a safe `OAuthAccount` object to store. This is the final guardrail after `oauth_route` selected the account during the browser return leg.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 74–127)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser-facing bridge for both halves of Pipedream consent: starting the hosted Connect page and receiving the browser back afterward. It exists because creating a Pipedream Connect link requires an asynchronous API call, while ufo’s normal authorization URL step is synchronous.

**Data flow**: It reads query parameters from the incoming request: provider, state, callback, and optionally an outcome marker. If required state or callback data is missing, it returns an error response. If the provider is unknown, it returns a not-found response. If the outcome says the connection succeeded, it finds the newest matching Pipedream account for this workspace-and-state-specific external user, then redirects to ufo’s callback with the original state and the account id as the code. If the outcome says anything else, it returns a clear failure response. If there is no outcome yet, it creates a Pipedream Connect token with success and error redirects pointing back to this same route, builds a hosted Pipedream Connect URL for the requested app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: The browser reaches this route first through `PipedreamOAuthProvider.authorize_url`. On the start leg, it calls the Pipedream client to mint a Connect token and sends the browser to Pipedream. On the return leg, it calls the Pipedream client again to identify the newly connected account and sends the browser back to ufo core, where `PipedreamOAuthProvider.exchange` later verifies and binds that account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 130–134)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the safe base origin from a full URL, such as `https://example.com`. It makes sure bridge URLs are built from a real HTTP or HTTPS URL with a host.

**Data flow**: It receives a URL string, parses it into parts, checks that the scheme is `http` or `https` and that a host is present, and returns only the scheme plus host. If the URL is missing those required parts, it raises a `ValueError` instead of building a broken or unsafe redirect.

**Call relations**: `PipedreamOAuthProvider.authorize_url` uses it to decide where the local bridge route should live, based on ufo’s callback URL. `oauth_route` uses it again when building the success and error return URLs that Pipedream should send the browser back to.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup, connection checks, and Slack conversation discovery`

This file turns Slack setup into actions an admin can run in conversation, instead of requiring manual database edits or hidden commands. It supports two ways to connect Slack. The easier path is OAuth, where UFO creates an “Add to Slack” link for an admin. The fallback path is a “bring your own Slack app” setup, where UFO prints a ready-made Slack app manifest and then waits for the bot token and signing secret to be collected privately.

The central idea is an install state machine: each call checks what is already known and returns a clear state such as not configured, not installed, pending, or connected. “Pending” means UFO has proved the Slack bot identity, but Slack has not yet successfully reached this deployment. “Connected” means Slack has sent a request that passed signature verification, proving the public URL and signing secret work.

The file also defines a runtime helper, slack_channels, which searches Slack channels and direct messages using the bot token. This matters because an agent may know a channel name or a person, not Slack’s internal conversation ID. Like a phone book, this tool helps translate human-friendly clues into the IDs Slack APIs need.

At the bottom, the file registers these handlers as tools bound to the Slack surface object, so the broader UFO tool system can expose and run them.

#### Function details

##### `_events_url`  (lines 145–146)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address where Slack should send events for this UFO deployment. This is the callback URL Slack needs in order to deliver messages, mentions, and setup verification requests.

**Data flow**: It takes the deployment’s public base URL, removes any trailing slash, then adds /surface/slack. The result is a complete Slack event endpoint URL as text.

**Call relations**: During connection checks, slack_connect_handler uses this to tell users which Slack-facing URL is involved. During manifest generation, slack_manifest_handler uses it to fill in the exact request URLs Slack should call.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 149–151)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Creates a standard tool response describing the current Slack setup state. It keeps all setup replies in the same simple JSON shape, so callers can reliably understand what to do next.

**Data flow**: It receives a state name, a human hint, an optional Slack events URL, and any extra details. It packs them into a dictionary, turns that dictionary into JSON text, wraps the text as tool content, and returns a ToolResult.

**Call relations**: The install flow calls this whenever it needs to report progress or a problem. _oauth_link, _derive_manifest_identity, and slack_connect_handler all use it so their different branches still produce the same kind of answer.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 154–195)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs one pass through Slack connection setup and reports where things stand. A user can call it repeatedly before, during, and after setup without starting over.

**Data flow**: It reads the current tool context, including credentials, stored identity data, public URL, and installation bindings. If no Slack identity is known, it either creates an OAuth install link or tries to derive identity from manually supplied credentials. Once identity exists, it binds this Slack team to the current UFO workspace, checks whether Slack has successfully reached this deployment, and returns a JSON state such as pending or connected.

**Call relations**: This is the main handler for the slack_connect tool. It delegates URL construction to _events_url, OAuth setup to _oauth_link, manifest-based setup to _derive_manifest_identity, status formatting to _state, and final reachability proof to _verified. It also reads Slack identity information and asks the Slack surface code for the stable installation ID used to bind a Slack workspace to UFO.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 198–228)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates an “Add to Slack” link for the deployment’s own Slack app. This is the preferred setup path because the admin can install the app with one guided Slack authorization flow.

**Data flow**: It checks whether the deployment has Slack app client credentials in environment variables. If not, it returns a not_configured state and points the user to the manifest path. If credentials exist, it checks that the speaker is an admin and that a public URL is configured. It then starts a sealed credential authorization handoff, builds Slack’s authorization URL, and returns it in a tool result.

**Call relations**: slack_connect_handler calls this when no Slack identity exists and the requested method is OAuth. This helper asks the tool context to begin credential authorization, then uses Slack surface helpers to build the Slack OAuth URL and returns the result through _state.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 231–265)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the manual “bring your own Slack app” setup path by proving that the supplied Slack credentials belong to a real bot and workspace. It turns privately collected secrets into the same identity record the OAuth path would produce.

**Data flow**: It checks the credential slots for the bot token and signing secret. If either is missing, it returns a not_configured state listing what still needs to be collected. If both are present, it reads the bot token, confirms the speaker is an admin, then asks SlackIdentityResolver to call Slack and resolve the team and bot identity. If Slack rejects the token or returns unusable identity data, it returns a helpful setup diagnosis instead.

**Call relations**: slack_connect_handler calls this when the user chooses the manifest setup method and identity is not already known. It uses _state for user-facing progress messages and _token_diagnosis to turn Slack token errors into clearer instructions.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 268–292)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has actually reached this UFO deployment using the currently valid signing secret. This is the difference between “we have credentials” and “Slack can successfully talk to us.”

**Data flow**: It reads the current verifying fingerprint, which is a safe marker derived from the signing secret. It then looks for a stored URL-verified marker in the workspace blob store. If the marker exists, is valid JSON, and matches the current fingerprint, it mirrors that proof into the extension store and returns true. Otherwise it returns false.

**Call relations**: slack_connect_handler calls this after identity and workspace binding are in place. The Slack surface writes the verification marker when it receives a correctly signed Slack request; this function reads that marker and updates the shared mirror through mirror_url_verified.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, mirror_url_verified, verifying_fingerprint).


##### `slack_manifest_handler`  (lines 295–310)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for manual Slack app setup. This saves users from hand-building scopes, event subscriptions, and request URLs in Slack’s app settings.

**Data flow**: It receives a requested bot display name and validates that it is short and plain enough for Slack. It reads the deployment’s public base URL, builds the Slack events URL, fills those values into the manifest template, and returns the manifest text as the tool result.

**Call relations**: This is the handler for the slack_app_manifest tool. It uses _events_url to produce the correct Slack request URL, then wraps the completed manifest in TextContent and ToolResult for the tool system to show to the user.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 313–336)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations such as channels, group direct messages, and one-to-one direct messages. It helps the agent find a Slack conversation by human clues like a name, topic, purpose, or people in a DM.

**Data flow**: It reads the Slack bot token from the extension’s credentials. If Slack is not connected or identity has not been resolved, it raises a clear error. Once it has the bot token and bot user ID, it runs SlackConversationSearch with the user’s query, converts the found conversations into plain data, and returns them as JSON marked untrusted because the names and topics come from Slack users.

**Call relations**: This is the handler for the slack_channels tool. It depends on read_identity to know which bot user is searching, then hands the actual Slack paging and matching work to SlackConversationSearch.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 339–345)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack authentication error codes into clearer setup advice. It helps users understand whether they likely copied the wrong token or whether Slack returned some other failure.

**Data flow**: It receives a Slack error string. If the error is one of the common token rejection cases, it returns a message telling the user to re-copy the Bot User OAuth Token. Otherwise it returns a more general auth.test failure message with the error included.

**Call relations**: _derive_manifest_identity calls this when SlackIdentityResolver reports an identity error during manifest-based setup. It provides the human-readable hint that _derive_manifest_identity includes in its not_configured state.

*Call graph*: called by 1 (_derive_manifest_identity).


### Web model account login
Web extension login flows let members bring their own Anthropic or OpenAI credentials and store verified account access for later use.

### `extensions/web/ufo_ext_web/anthropic_login.py`

`domain_logic` · `request handling during Anthropic sign-in and credential verification`

This file is the bridge between UFO and Anthropic sign-in. Its job is to make sure a member can bring their own Anthropic access, and that UFO only accepts credentials Anthropic actually recognizes. Without this, users could not safely attach their Anthropic account or key, and the app might store useless or mistyped secrets.

There are two paths implied here. If UFO has an Anthropic OAuth client ID, `AnthropicCodeLogin` starts a browser-based authorization flow. It creates a one-time secret called a verifier, turns it into a matching challenge, and builds the Anthropic authorization URL. Anthropic later shows the user a code rather than redirecting back to this app, so the user copies and pastes that code into UFO. The app then exchanges the pasted code plus the original verifier for an access token.

The file is careful about trust. It accepts the different paste formats Anthropic may show: a plain code, a `code#state` pair, or a full callback URL. If a returned state is present, it must match the browser’s saved verifier, like checking that a coat-check ticket belongs to the same person who started the flow.

Finally, `verified_key` tests either an OAuth access token or an API key by asking Anthropic for the model list. Only credentials that get a successful answer should move on to storage.

#### Function details

##### `AnthropicCodeLogin.authorize`  (lines 73–96)

```
def authorize(self) -> PendingAuthorization
```

**Purpose**: This starts the Anthropic authorization-code sign-in. It creates the special one-time values needed to send the user to Anthropic safely, and returns both the link to open and the browser cookie value UFO must remember.

**Data flow**: It starts with the configured Anthropic client ID, authorization URL, and redirect URI. It makes a random verifier, turns that into a hashed challenge, places those values into a web address query string, and returns a `PendingAuthorization` containing the finished Anthropic URL plus the verifier to save in the user’s browser cookie.

**Call relations**: This is called when UFO needs to begin the Anthropic login journey. It relies on standard encoding, hashing, random-byte generation, and URL-building helpers to create the authorization link, then hands the browser a URL to visit and a cookie value that `AnthropicCodeLogin.claim` will later need to redeem the pasted code.

*Call graph*: 5 external calls (__init__, urlsafe_b64encode, sha256, token_bytes, urlencode).


##### `AnthropicCodeLogin.claim`  (lines 98–132)

```
async def claim(self, pasted: str, verifier: str) -> Grant | None
```

**Purpose**: This finishes the Anthropic code sign-in after the user pastes back what Anthropic showed them. It checks that the pasted information belongs to the same login attempt, then asks Anthropic to trade the code for an access grant.

**Data flow**: It receives the pasted text and the verifier saved from the earlier browser cookie. It first rejects missing verifier data, then uses `_split_pasted` to pull out the code and optional returned state. If the state is present, it compares it safely with the saved verifier. If the values look valid, it sends a JSON request to Anthropic’s token endpoint. A successful response is parsed into a `Grant`; network errors, rejected codes, bad status codes, or malformed grant data all produce `None` instead.

**Call relations**: This runs after `AnthropicCodeLogin.authorize` has sent the user through Anthropic and the user has pasted a result back into UFO. It delegates paste-format cleanup to `_split_pasted`, uses an HTTP client to contact Anthropic, and passes Anthropic’s JSON answer to `ufo.sdk.models.granted` so the rest of the system receives a normal UFO grant object rather than raw response data.

*Call graph*: calls 1 internal fn (_split_pasted); 4 external calls (AsyncClient, dumps, compare_digest, granted).


##### `_split_pasted`  (lines 135–147)

```
def _split_pasted(pasted: str) -> tuple[str, str | None]
```

**Purpose**: This small helper understands the different forms Anthropic may give the user to paste back. It extracts the authorization code and, when available, the matching state value.

**Data flow**: It receives the pasted text and trims extra spaces. If the text looks like a full URL, it reads both the URL query string and fragment to find `code` and `state`. If the text contains a `#`, it treats the part before it as the code and the part after it as the state. Otherwise, it treats the whole pasted value as the code and returns no state.

**Call relations**: It is used by `AnthropicCodeLogin.claim` before any token exchange happens. Its role is to make the user-facing paste step forgiving, so `claim` can work with a clean code-and-state pair instead of having to care which Anthropic display format the user copied.

*Call graph*: called by 1 (claim); 2 external calls (parse_qs, urlsplit).


##### `verified_key`  (lines 150–164)

```
async def verified_key(credential: str) -> bool
```

**Purpose**: This checks whether a provided Anthropic credential really works before UFO accepts it. It supports both OAuth access tokens and regular Anthropic API keys.

**Data flow**: It receives a credential string. If the string looks like an Anthropic OAuth access token, it builds request headers for bearer-token authentication and includes Anthropic’s OAuth beta header. Otherwise, it treats the value as an API key and sends it as `x-api-key`. It then asks Anthropic’s models endpoint for a response. If the network request fails or Anthropic does not return success, the result is `False`; a successful response gives `True`.

**Call relations**: This is used after a user supplies or obtains an Anthropic credential, before that credential is trusted by the rest of the system. It does not call the code-flow methods directly; instead, it is the final gate that confirms Anthropic will actually answer for the value.

*Call graph*: 1 external calls (AsyncClient).


### `extensions/web/ufo_ext_web/openai_login.py`

`io_transport` · `user sign-in request handling`

This file is the bridge between the UFO web portal and OpenAI’s device sign-in flow. A device sign-in is the kind of login where one screen shows a short code, and the user types that code into a separate OpenAI page to approve access. It is like a hotel front desk giving you a claim ticket: the ticket itself is not the room key, but it lets the system match your later approval to the right request.

The main class, OpenAiDeviceLogin, does the two important steps. First, request_code asks OpenAI to start a sign-in and returns the code and web address the member should use. Second, claim checks whether the member has approved the code yet. If approval is still pending, it says so. If OpenAI has approved it, claim trades the temporary authorization code for a real token.

The file is careful about failures. Network errors, missing fields, bad responses, or tokens that do not look like usable ChatGPT account tokens are turned into simple states: pending, granted, or refused. That gives the web page a clean story to show the user instead of exposing raw OpenAI errors. The resulting token is meant for the member’s own credential slot, so later work can use the member’s access before falling back to any admin-provided key.

#### Function details

##### `OpenAiDeviceLogin.request_code`  (lines 85–103)

```
async def request_code(self) -> DeviceAuthorization | None
```

**Purpose**: Starts an OpenAI device sign-in and asks OpenAI for the short code the user must type into OpenAI’s verification page. It returns the code information if OpenAI starts the flow successfully, or nothing if the sign-in cannot be opened.

**Data flow**: It begins with the configured OpenAI client id and the device-code URL. It sends those to OpenAI over HTTP. If the network call fails, the response is not successful, or the needed fields are missing, the result is no code. If OpenAI returns a usable device authorization id and user code, the function packages them with the verification page address and a safe polling interval, then returns that package.

**Call relations**: This is the first leg of the sign-in story. The web portal calls on it before showing the OpenAI login instructions, so the page can include the member’s actual code. It relies on _interval to turn OpenAI’s suggested waiting time into a usable number, then hands back a DeviceAuthorization object for the rest of the login flow.

*Call graph*: calls 1 internal fn (_interval); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin.claim`  (lines 105–126)

```
async def claim(self, device_auth_id: str, user_code: str) -> DeviceClaim
```

**Purpose**: Checks whether the user has approved the device code at OpenAI yet. If approval happened, it continues the process and tries to turn that approval into a stored token.

**Data flow**: It receives the device authorization id and user code from the earlier step. It sends both back to OpenAI’s device token endpoint. A temporary network problem becomes a pending result, because the user may still be approving. A non-success response is interpreted by _unapproved. A successful response must contain an authorization code and a code verifier; if either is missing, the claim is refused. If both are present, it passes them to _redeem, which tries to buy the final token.

**Call relations**: This is the polling leg of the sign-in story. The web page or backend calls it repeatedly after request_code has shown the user a code. When OpenAI says the user has not approved yet, claim returns a simple pending status. When OpenAI says approval is ready, claim hands off to _redeem to finish the exchange.

*Call graph*: calls 2 internal fn (_redeem, _unapproved); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin._redeem`  (lines 128–153)

```
async def _redeem(self, client: httpx.AsyncClient, code: str, verifier: str) -> DeviceClaim
```

**Purpose**: Turns OpenAI’s temporary approval into the actual ChatGPT account token the system can store and use later. It is a private helper because it only makes sense after claim has received a valid approval response.

**Data flow**: It takes an HTTP client, an authorization code, and a verifier. It sends them to OpenAI’s normal token endpoint using form data, along with the client id and redirect address. If the request fails or OpenAI rejects it, the result is refused. If OpenAI returns data, the function tries to parse it as a granted token and checks that the token identifies a ChatGPT account. Only then does it return a granted result containing the stored form of the key.

**Call relations**: OpenAiDeviceLogin.claim calls this after OpenAI confirms the user approved the device sign-in. _redeem then calls the shared token helpers granted and chatgpt_account_id to make sure the response is both understandable and useful before handing a granted DeviceClaim back to claim’s caller.

*Call graph*: called by 1 (claim); 4 external calls (__init__, post, chatgpt_account_id, granted).


##### `_interval`  (lines 156–162)

```
def _interval(raw: object) -> int
```

**Purpose**: Converts OpenAI’s suggested polling delay into an integer number of seconds. If OpenAI sends nothing useful, it falls back to a safe default so the system does not poll too aggressively.

**Data flow**: It receives any raw value, often a string from OpenAI’s JSON response. It strips and converts that value to an integer when possible. If conversion fails, it returns the default polling interval instead.

**Call relations**: OpenAiDeviceLogin.request_code uses this helper after OpenAI starts a device sign-in. The helper keeps request_code focused on opening the sign-in while still ensuring the caller gets a sensible wait time before checking for approval.

*Call graph*: called by 1 (request_code).


##### `_unapproved`  (lines 165–177)

```
def _unapproved(polled: httpx.Response) -> DeviceClaim
```

**Purpose**: Interprets OpenAI responses that are not a normal success during polling. It separates “the user has not approved yet” from “this sign-in cannot continue.”

**Data flow**: It receives an HTTP response from a polling attempt. Certain status codes immediately become a pending result. Otherwise it tries to read OpenAI’s error code from the response body. Known pending-style error codes also become pending. Anything else becomes a refused result with a user-facing message saying the device sign-in could not start or continue.

**Call relations**: OpenAiDeviceLogin.claim calls this whenever OpenAI’s polling endpoint does not return success. This keeps the polling flow simple: claim asks OpenAI, and _unapproved translates awkward OpenAI failure shapes into the clean DeviceClaim states the rest of the web flow understands.

*Call graph*: called by 1 (claim); 2 external calls (__init__, json).
